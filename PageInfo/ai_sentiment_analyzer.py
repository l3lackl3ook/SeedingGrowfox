from openai import OpenAI
import os
import json
import re
from dotenv import load_dotenv

# ✅ โหลด environment variables
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def analyze_sentiment_and_category(content, image_url=None, post_product="Hygiene"):
    """
    วิเคราะห์คอมเมนต์ Facebook สำหรับโพสต์ Hygiene
    """
    prompt = f"""
    วิเคราะห์คอมเมนต์ Facebook ด้านล่างนี้ในฐานะผู้เชี่ยวชาญ Consumer Insight โดยมีเงื่อนไขดังนี้:

    ⭐️ **Context โพสต์**
    - Product: {post_product}
    - Image URL (ถ้ามี): {image_url}
    
    โพสต์นี้เป็นภาพของผลิตภัณฑ์ ไฮยีนเอ็กซ์เพิร์ทแคร์ เบอร์รี่โทสต์ ชมพูอ่อน ลายเค้ก

    ⭐️ **โจทย์**
    1. หากคอมเมนต์เป็นแค่แท็กชื่อเพื่อนหรือ mention author โดยไม่มีข้อความอื่น ให้ category = "แท็กเพื่อน" และ sentiment = "neutral"
    2. หากคอมเมนต์ขึ้นต้นด้วยชื่อ author แล้วมีข้อความต่อ ให้ category = "ตอบกลับ" และ sentiment = "neutral"
    3. หากคอมเมนต์กล่าวถึงสีใด ให้ถือว่าเป็น Positive category = "ใช้ดี"
    4. หากไม่มีข้อความในคอมเมนต์ แต่มี image_url ให้ดูจากรูปภาพว่ามีสีใดใน 7 สีข้างต้น และจัดเป็น Positive category = "ใช้ดี"
    5. หากมีเนื้อหาเกี่ยวกับสินค้าให้จัด category ตามนี้ (*เลือกอย่างเดียว ห้าม "อื่นๆ" เว้นแต่ไม่มีข้อมูลจริง*):
      - ใช้ดี
      - กลิ่นหอม
      - ราคา
      - ซื้อที่ไหน
      - ไม่หอม
      - คราบไม่ออก

    ⭐️ **Sentiment**:
      - Positive: ใช้ดี, กลิ่นหอม, ออกทุกคราบ, น่าลอง, ตามบ้าง, ยี่ห้อนี้ดี, คอนเฟิร์ม, กล่าวถึงสี (เช่น ขาว, ฟ้า, ชมพู), ถ่ายรูปสินค้า
      - neutral: ซื้อที่ไหน, ราคา, แท็กเพื่อน, ตอบกลับ
      - negative: ไม่หอม, กลิ่นฉุน, คราบไม่ออก

    ⭐️ **Output ที่ต้องตอบกลับ (JSON format เท่านั้น)**

    {{
        "sentiment": "Positive", // ตัว P ใหญ่ หรือ neutral / negative ตามนิยามด้านบน
        "reason": "เหตุผลสั้น ๆ กระชับในมุมมองผู้เชี่ยวชาญ เช่น ถ่ายรูปสีฟ้าแสดงถึงความชอบ",
        "keyword_group": "เลือกคีย์เวิร์ดเดียวจากคอมเมนต์ หรือชื่อผลิตภัณฑ์/สี Hygiene ด้านบน (*ห้ามตอบหลายคำ*)",
        "category": "หมวดหมู่ตามที่กำหนดด้านบน"
    }}

    ⭐️ **ห้าม**:
    - ใส่ backtick
    - ตอบหลายค่าใน field เดียว
    - ตอบข้อความนอก JSON

    🎯 **คอมเมนต์**:
    "{content}"

    🎯 **Image URL**:
    "{image_url}"
    """

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are an expert Thai sentiment categorizer and consumer insight analyst."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.2
    )

    try:
        raw = response.choices[0].message.content

        # 🔧 clean backtick หรือ ```json
        clean = re.sub(r"```json|```", "", raw).strip()

        # 🔧 หากยังมีข้อความเกิน JSON, ดึงเฉพาะ {...}
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            result = json.loads(match.group())
        else:
            raise ValueError("JSON not found in AI response")

        # ✅ แก้ sentiment format ตามโจทย์
        sentiment = result.get("sentiment", "").strip()
        if sentiment.lower() == "positive":
            sentiment = "Positive"
        elif sentiment.lower() == "neutral":
            sentiment = "neutral"
        elif sentiment.lower() == "negative":
            sentiment = "negative"
        else:
            sentiment = ""

        final_result = {
            "sentiment": sentiment,
            "reason": result.get("reason", "").strip(),
            "keyword_group": result.get("keyword_group", "").strip(),
            "category": result.get("category", "").strip(),
        }

        return final_result

    except Exception as e:
        print("❌ Error parsing AI response:", e)
        print("Raw response:", raw)
        return {"sentiment": "", "reason": "", "keyword_group": "", "category": ""}
