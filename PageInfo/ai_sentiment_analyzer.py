from openai import OpenAI
import os
import json
import re
from dotenv import load_dotenv

# ✅ โหลด environment variables
load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def analyze_sentiment_and_category(content, image_url=None):
    """
    วิเคราะห์คอมเมนต์สำหรับแบรนด์ Ivy โดยเฉพาะ
    """
    prompt = f"""
    วิเคราะห์คอมเมนต์ Facebook ด้านล่างนี้ สำหรับแบรนด์นมเปรี้ยว Ivy ในฐานะผู้เชี่ยวชาญ Consumer Insight

    คอมเมนต์: "{content}"

    - จัดประเภท sentiment เป็น "Positive" (ตัว P ใหญ่), หรือ "neutral"/"negative" (ตัว n เล็ก)
    - เหตุผล (reason) ที่จัดหมวดหมู่นี้ (สั้น กระชับ)
    - Keyword Group (คีย์เวิร์ดหลัก) จากเนื้อหา เช่น อร่อย, หวาน, สดชื่น, ราคา, พิกัด, หวานตัดขา, กินประจำ
    - Category (หมวดหมู่) เช่น ความรู้สึกต่อรสชาติ, ความหวาน, ความสดชื่น, ราคาและโปรโมชั่น, คำชม, คำติ, หรือ อื่นๆ

    ⭐️ ตอบกลับเป็น JSON เท่านั้น:
    {{
        "sentiment": "Positive",
        "reason": "อร่อยและสดชื่น",
        "keyword_group": "อร่อย",
        "category": "ความรู้สึกต่อรสชาติ"
    }}

    ❌ ห้ามใส่ข้อความอื่นนอกจาก JSON, ห้ามใส่ backtick, ห้ามตอบหลายค่าใน field เดียว
    """

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are an expert Thai sentiment categorizer."},
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
