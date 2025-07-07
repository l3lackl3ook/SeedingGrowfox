import json
import re
import asyncio
import time
from pathlib import Path
from pprint import pprint
from typing import Any, Optional, List, Tuple

from playwright.async_api import Playwright, async_playwright, Browser, Page, BrowserContext
from datetime import datetime

class FBReelScraperAsync:
    def __init__(self, cookie_file: str, headless: bool = False,
                 page_url: Optional[str] = None, cutoff_dt: datetime = None,
                 batch_size: int = 10):
        self.cookie_file = cookie_file
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.page_url = page_url
        self.cutoff_dt = cutoff_dt
        self.batch_size = batch_size

        # JavaScript snippet to fetch posts (Reels only, no cutoff/date logic)
        JS_FETCH_POSTS = r"""() => {
    // Find all reel links under the Reels section
    const anchors = Array.from(document.querySelectorAll('div[data-pagelet="ProfileAppSection_0"] a[href*="/reel/"]'));
    return anchors.map(a => {
        const href = a.href;
        // Thumbnail is the first img inside the same anchor
        const img = a.querySelector('img');
        const thumbnail = img ? img.src : null;
        // Find watch count sibling within the same card
        let watchCount = null;
        // The watch count span is usually a sibling of the img within the same link container
        const countSpan = a.parentElement.querySelector('span.x1lliihq.x6ik8m8r.x10wlt62.x1n2onr6') 
                         || a.querySelector('span.x1lliihq.x6ikm8r.x10wlt62.x1n2onr6');
        if (countSpan) watchCount = countSpan.innerText.trim();
        return { id: href, thumbnail, watchCount };
    });
}
"""
        self.JS_FETCH_POSTS = JS_FETCH_POSTS

    async def _scroll_and_eval(self, page, cutoff_ms):
        # Scroll to load more posts, then run the fetch JS
        await page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
        await page.wait_for_timeout(3000)
        return await page.evaluate(self.JS_FETCH_POSTS, cutoff_ms)

    async def _process_cookie(self) -> List[dict]:
        raw = json.loads(Path(self.cookie_file).read_text())
        for cookie in raw:
            s = cookie.get("sameSite")
            if s is None or (isinstance(s, str) and s.lower() == "no_restriction"):
                cookie["sameSite"] = "None"
            elif isinstance(s, str) and s.lower() == "lax":
                cookie["sameSite"] = "Lax"
            elif isinstance(s, str) and s.lower() == "strict":
                cookie["sameSite"] = "Strict"
        return raw

    async def _confirm_login(self, page: Page) -> Optional[str]:
        try:
            # Wait for the navigation role element with aria-label "ทางลัด"
            nav = page.get_by_role("navigation", name="ทางลัด")
            await nav.wait_for(timeout=5000)
            # The first link inside nav is the user profile; its text is the username
            profile_link = nav.get_by_role("link").first
            await profile_link.wait_for(timeout=5000)
            username = (await profile_link.inner_text()).strip()
            return username
        except Exception as e:
            print(f"[confirm_login] failed to confirm login: {e}")
            return None

    def _parse_thai_timestamp(self, text: str) -> datetime:
        thai_months = {
            "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4,
            "พฤษภาคม": 5, "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8,
            "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12
        }
        parts = text.split()
        # Handle dates with only day and month (e.g., '23 กุมภาพันธ์')
        if len(parts) == 2 and parts[0].isdigit():
            day = int(parts[0])
            month = thai_months.get(parts[1], 0)
            year = datetime.now().year
            return datetime(year, month, day)
        try:
            # Try parsing "วัน...ที่ DD Month YYYY เวลา hh:mm น."
            if len(parts) >= 5 and parts[3].isdigit():
                day = int(parts[1])
                month_name = parts[2]
                month = thai_months.get(month_name, 0)
                year = int(parts[3])
                time_part = parts[5]
            else:
                # No year provided; use current year
                day = int(parts[1])
                month_name = parts[2]
                month = thai_months.get(month_name, 0)
                year = datetime.now().year
                time_part = parts[4]  # "hh:mm"
            hour_str, minute_str = time_part.split(":")
            hour = int(hour_str)
            minute = int(minute_str)
            return datetime(year, month, day, hour, minute)
        except Exception:
            return datetime(1970, 1, 1)

    def _parse_thai_number(self, text: str) -> int:
        """Convert a Thai-formatted count (e.g. '1.2 พัน', '5 หมื่น') to an integer."""
        import re
        units = {'พัน': 10**3, 'หมื่น': 10**4, 'แสน': 10**5, 'ล้าน': 10**6}
        t = text.strip()
        # Check for known units
        for unit, mul in units.items():
            if t.endswith(unit):
                num_str = t[:-len(unit)].strip()
                try:
                    value = float(num_str)
                except ValueError:
                    value = 1.0
                return int(value * mul)
        # Fallback: strip non-digits and parse
        digits = re.sub(r'[^\d]', '', t)
        return int(digits) if digits else 0

    async def _get_post(self, page: Page, max_posts: int, seen_ids: set) -> Tuple[List[Tuple[str, str, int]], bool]:
        """
        Fetch up to max_posts Reels. Returns a list of tuples (url, thumbnail, watchCount)
        and a boolean indicating if no more posts are available (always False here).
        """
        batch = []
        empty_fetch_retries = 0
        max_empty_fetch_retries = 3

        # Navigate to the Reels tab on first fetch
        if not seen_ids:
            reels_page_url = f"{self.page_url.rstrip('/')}/reels"
            await page.goto(reels_page_url)
        # Wait for the Reels container
        await page.wait_for_selector('div[data-pagelet="ProfileAppSection_0"]', timeout=10000)

        # Loop until we collect enough posts or exhaust retries
        while len(batch) < max_posts and empty_fetch_retries < max_empty_fetch_retries:
            # Execute the JS snippet to retrieve current reels
            data = await page.evaluate(self.JS_FETCH_POSTS)
            if not data:
                empty_fetch_retries += 1
                # Scroll and retry loading more reels
                await page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
                await page.wait_for_timeout(2000)
                continue

            # Count how many new reels we append this iteration
            new_count = 0
            for entry in data:
                url = entry.get("id")
                thumbnail = entry.get("thumbnail")
                watch_count_text = entry.get("WatchCount") if "WatchCount" in entry else entry.get("watchCount")
                watch_count = self._parse_thai_number(watch_count_text) if watch_count_text else None
                if url and url not in seen_ids:
                    batch.append((url, thumbnail, watch_count))
                    seen_ids.add(url)
                    new_count += 1
                    if len(batch) >= max_posts:
                        break

            if new_count == 0:
                # No new reels fetched; count as empty fetch
                empty_fetch_retries += 1
                await page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
                await page.wait_for_timeout(2000)
                continue
            else:
                # Reset retry counter on successful fetch
                empty_fetch_retries = 0

            if len(batch) >= max_posts:
                # print(batch)
                break

            # Load more content if needed
            await page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
            await page.wait_for_timeout(2000)

        # This scraper does not use a cutoff, so older_than_cutoff always False
        return batch, False

    async def _get_post_detail(self, context: BrowserContext, reel_url: str, reel_thumbnail: str, watch_count: int) -> Optional[dict]:
        try:
            # print(f"[get_post_detail] Opening detail page for: {reel_url}")
            detail_page = await context.new_page()
            await detail_page.goto(reel_url)
            # Disable video autoplay
            await detail_page.evaluate("""
                document.querySelectorAll('video').forEach(v => {
                    v.pause();
                    v.autoplay = false;
                });
            """)

            postRoot = detail_page.locator('div.x6s0dn4.x78zum5.xdt5ytf.x5yr21d.x1o0tod.xl56j7k.x10l6tqk.x13vifvy.xh8yej3').first
            await postRoot.wait_for(state='visible')

            # Standardize post_url and extract post_id
            post_url = reel_url.split('?')[0]
            post_id_match = re.search(r'/reel/(\d+)', post_url)
            post_id = post_id_match.group(1) if post_id_match else None
            post_type = "reel"

            # Extract post content, expanding "ดูเพิ่มเติม" if present
            content_locator = detail_page.locator('div.xyamay9.xv54qhq.xf7dkkf.xjkvuk6')
            # Click the "ดูเพิ่มเติม" button *within* the content container
            more_btn = content_locator.locator('div[role="button"]:has-text("ดูเพิ่มเติม")')
            if await more_btn.count() > 0:
                await more_btn.first.click()
                # Wait until the "ดูน้อยลง" button appears, indicating content expanded
                await content_locator.locator('div[role="button"]:has-text("ดูน้อยลง")').wait_for(timeout=5000)
            # Now retrieve the full expanded content
            post_content = (await content_locator.text_content()).strip()
            # Remove trailing 'ดูน้อยลง' and any embedded CSS
            if 'ดูน้อยลง' in post_content:
                post_content = post_content.split('ดูน้อยลง')[0].strip()
            # Strip out any scrollbar CSS remnants
            post_content = re.sub(r'\s*::-webkit-scrollbar[\s\S]*', '', post_content).strip()

            # Extract video URL
            video_url = post_url

            # Extract only the 'ถูกใจ' reaction, comment_count, and share_count
            react_count = {}
            comment_count = 0
            share_count = 0
            # Narrow to the reactions panel
            reaction_panel = postRoot.locator('div.x11hdunq.x100vrsf').first
            # LIKE
            like_btn = reaction_panel.locator('div[aria-label="ถูกใจ"]').first
            if await like_btn.count():
                # The count is in the next sibling div of the button's outer container
                like_count_el = like_btn.locator(
                    'xpath=ancestor::div[contains(@class,"__fb-dark-mode")]/following-sibling::div//span[contains(@class,"x1lliihq")]'
                ).first
                if await like_count_el.count():
                    react_count['ถูกใจ'] = self._parse_thai_number((await like_count_el.inner_text()).strip())
                else:
                    react_count['ถูกใจ'] = 0
            else:
                react_count['ถูกใจ'] = 0
            # COMMENT
            comment_btn = reaction_panel.locator('div[aria-label="แสดงความคิดเห็น"]').first
            if await comment_btn.count():
                comment_count_el = comment_btn.locator(
                    'xpath=ancestor::div[contains(@class,"__fb-dark-mode")]/following-sibling::div//span[contains(@class,"x1lliihq")]'
                ).first
                if await comment_count_el.count():
                    comment_count = self._parse_thai_number((await comment_count_el.inner_text()).strip())
            # SHARE
            share_btn = reaction_panel.locator('div[aria-label="แชร์"]').first
            if await share_btn.count():
                share_count_el = share_btn.locator(
                    'xpath=ancestor::div[contains(@class,"__fb-dark-mode")]/following-sibling::div//span[contains(@class,"x1lliihq")]'
                ).first
                if await share_count_el.count():
                    share_count = self._parse_thai_number((await share_count_el.inner_text()).strip())

            # print(f"[get_post_detail] Successfully fetched details for {post_id}")
            await detail_page.close()

            return {
                "post_url": post_url,
                "post_id": post_id,
                "post_type": post_type,
                "post_content": post_content,
                "video_thumbnail": reel_thumbnail,
                "video_url": video_url,
                "reactions": react_count,
                "comment_count": comment_count,
                "share_count": share_count,
                "watch_count": watch_count,
            }

        except Exception as e:
            print(f"[get_post_detail] ERROR for {post_url}: {e}")
            try:
                await detail_page.close()
            except:
                pass
            return None

    # async def _get_post_comments(self, page: Page) -> list:
    #     comments = []
    #     try:
    #         # Wait for and click the comments sort button
    #         await page.wait_for_selector('div.x6s0dn4.x78zum5.xdj266r.x14z9mp.xat24cr.x1lziwak.xe0p6wg', timeout=10000)
    #         await page.click('div.x6s0dn4.x78zum5.xdj266r.x14z9mp.xat24cr.x1lziwak.xe0p6wg')
    #         # Open the full comments dialog
    #         await page.click('div[role="menuitem"] >> text="ความคิดเห็นทั้งหมด"')
    #         # 1) Wait for the comments dialog Locator to appear
    #         dialog = page.locator('div[role="dialog"]').first
    #         await dialog.wait_for(timeout=10000)
    #         # Find the actual scrollable element inside the dialog via computed styles
    #         dialog_handle = await dialog.element_handle()
    #         scrollable_handle = await page.evaluate_handle(
    #             """dialog => {
    #                 const divs = Array.from(dialog.querySelectorAll('div'));
    #                 return divs.find(el => {
    #                     const style = window.getComputedStyle(el);
    #                     return style.overflowY === 'auto' || style.overflowY === 'scroll';
    #                 }) || dialog;
    #             }""",
    #             dialog_handle
    #         )
    #         # Scroll until no new comments load
    #         prev_count = 0
    #         while True:
    #             # Count loaded top-level comments
    #             curr_count = await page.locator('div[role="article"][aria-label^="ความคิดเห็นจาก"]').count()
    #             if curr_count > prev_count:
    #                 prev_count = curr_count
    #                 await scrollable_handle.evaluate("el => el.scrollTo(0, el.scrollHeight)")
    #                 await page.wait_for_timeout(1000)
    #             else:
    #                 break
    #     except Exception as e:
    #         print(f"[get_post_comments] Failed to open comments menu: {e}")
    #         return comments
    #
    #     # Wait for at least one top-level comment to load
    #     try:
    #         await page.wait_for_selector('div[role="article"][aria-label^="ความคิดเห็นจาก"]', timeout=10000)
    #     except Exception:
    #         # No top-level comments present; exit gracefully
    #         return []
    #
    #     # Select only main comment containers (exclude replies)
    #     comment_divs = await page.locator('div[role="article"][aria-label^="ความคิดเห็นจาก"]').all()
    #     for div in comment_divs:
    #         try:
    #             image_element = div.locator('svg image').first
    #             profile_img = await image_element.get_attribute('xlink:href')
    #
    #             # Extract commenter name and profile URL from the visible name link
    #             visible_links = div.locator('a[aria-hidden="false"]')
    #             if await visible_links.count():
    #                 name_link = visible_links.first
    #             else:
    #                 name_link = div.locator('a').first
    #             await name_link.wait_for(timeout=5000)
    #             profile_name = (await name_link.text_content()).strip()
    #             href = await name_link.get_attribute('href')
    #             profile_url = href.split('?')[0] if href else None
    #
    #             # Extract just the comment message
    #             # Look for the nested div with dir="auto" inside the comment body
    #             msg_locator = div.locator('div.x1lliihq.xjkvuk6.x1iorvi4 div[dir="auto"]').first
    #             if await msg_locator.count():
    #                 comment_text = (await msg_locator.inner_text()).strip()
    #             else:
    #                 comment_text = ''
    #
    #             # Hover to reveal timestamp tooltip
    #             time_link = div.locator('a[href*="?comment_id="]').last
    #             try:
    #                 await time_link.hover()
    #                 await page.wait_for_selector('div[role="tooltip"]', timeout=5000)
    #                 tooltip = page.locator('div[role="tooltip"]').first
    #                 time_stamp_text = (await tooltip.inner_text()).strip()
    #                 time_stamp_dt = self._parse_thai_timestamp(time_stamp_text)
    #             except:
    #                 time_stamp_text = None
    #                 time_stamp_dt = None
    #
    #             comments.append({
    #                 "user_name": profile_name,
    #                 "profile_url": profile_url,
    #                 "profile_img": profile_img,
    #                 "comment_text": comment_text,
    #                 "time_stamp_text": time_stamp_text,
    #                 "time_stamp_dt": time_stamp_dt,
    #             })
    #         except Exception as e:
    #             print(f"[get_post_comments] Error extracting a comment: {e}")
    #             print(page.url)
    #             continue
    #
    #     return comments

    async def run(self) -> None:
        print("Starting scraper...")
        async with async_playwright() as pw:
            launch_args = {"headless": self.headless}
            self.browser = await pw.chromium.launch(**launch_args)
            print("Browser launched.")
            context_args = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"

            }
            self.context = await self.browser.new_context(**context_args)
            cookie_list = await self._process_cookie()
            await self.context.add_cookies(cookie_list)

            # ---------------------
            # 1) Confirm login
            # ---------------------
            self.page = await self.context.new_page()
            await self.page.goto("https://www.facebook.com/")
            username = await self._confirm_login(self.page)
            print(f"Login as: {username or 'unknown'}")
            if not username:
                print("Login failed, stopping.")
                return

            # ---------------------
            # 2) Get page name
            # ---------------------
            if self.page_url:
                try:
                    await self.page.goto(self.page_url)
                    title_container = self.page.locator(
                        "div.x9f619.x1n2onr6.x1ja2u2z.x78zum5.xdt5ytf.x2lah0s.x193iq5w.x1cy8zhl.xexx8yu"
                    ).first
                    await title_container.wait_for(timeout=10000)
                    raw_page_name = await title_container.locator("h1.html-h1").text_content()
                    page_name = raw_page_name.split("\u00A0")[0].strip()
                    print(f"Page name: {page_name}")
                    print(f"Cutoff datetime: {self.cutoff_dt}")

                    reel_page_url = f"{self.page_url.rstrip('/')}/reels"
                    await self.page.goto(reel_page_url)
                except Exception as e:
                    print(f"Failed to open Facebook Page: {e}")
                    return

            # ---------------------
            # 3) Collect posts and fetch details in batches
            # ---------------------
            seen_ids = set()
            all_results = []

            batch_index = 1
            cutoff_dt = self.cutoff_dt
            empty_batch_retries = 0
            max_empty_batch_retries = 3
            while True:
                print(f"Collecting batch {batch_index} of reels...")
                # Wait for the video card selector to appear before collecting posts
                await self.page.wait_for_selector('div[data-pagelet="ProfileAppSection_0"]', timeout=10000)
                batch_posts, older = await self._get_post(
                    page=self.page,
                    max_posts=self.batch_size,
                    seen_ids=seen_ids
                )
                if not batch_posts:
                    if empty_batch_retries < max_empty_batch_retries:
                        empty_batch_retries += 1
                        print(f"No posts fetched; retrying scroll ({empty_batch_retries}/{max_empty_batch_retries})")
                        await self.page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
                        await self.page.wait_for_timeout(500)
                        continue
                    else:
                        print("No posts fetched after retries; exiting.")
                        break
                # Reset retry counter when posts are fetched
                empty_batch_retries = 0

                print(f"Found {len(batch_posts)} reels in batch {batch_index}.")
                print("Getting post details for this batch...")

                # Process fetched posts...
                tasks = [
                    self._get_post_detail(self.context, post_url, thumbnail, watch_count)
                    for (post_url, thumbnail, watch_count) in batch_posts
                ]
                batch_results = await asyncio.gather(*tasks)
                for detail in batch_results:
                    if detail:
                        all_results.append(detail)
                        pprint(detail)

                # After processing, if we hit older posts, exit
                if older:
                    print("Reached cutoff after processing; exiting.")
                    break

                batch_index += 1
                # Scroll down for the next batch
                print("Scrolling down for next batch...")
                await self.page.evaluate("window.scrollBy(0, document.body.scrollHeight);")
                await self.page.wait_for_timeout(500)

            print(f"Fetched all reels details. Total reels: {len(all_results)}")

            # ---------------------
            # 5) Cleanup
            # ---------------------
            await self.context.close()
            await self.browser.close()
        print("Scraper finished.")
        return all_results

    def start(self):
        """Synchronous entry point to launch the async run."""
        return asyncio.run(self.run())


if __name__ == "__main__":
    scraper = FBReelScraperAsync(
        cookie_file="cookie.json",
        headless=True,
        page_url="https://www.facebook.com/hartbeatfanpage",
        # cutoff_dt=datetime(2025, 5, 1, 0, 0),
        cutoff_dt= None,
        batch_size = 10
    )
    scraper.start()