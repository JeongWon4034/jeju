import streamlit as st
import pandas as pd
import requests
import re

from openai import OpenAI
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# CSV 데이터 로드
data = pd.read_csv("cj_data_final.csv", encoding="cp949").drop_duplicates()

# 카페 포맷 함수
def format_cafes(cafes_df):
    cafes_df = cafes_df.drop_duplicates(subset=['c_name', 'c_value', 'c_review'])
    result = []

    if len(cafes_df) == 0:
        return ("☕ 현재 이 관광지 주변에 등록된 카페 정보는 없어요.  \n"
                "하지만 근처에 숨겨진 보석 같은 공간이 있을 수 있으니,  \n"
                "지도를 활용해 천천히 걸어보시는 것도 추천드립니다 😊")

    elif len(cafes_df) == 1:
        row = cafes_df.iloc[0]
        if all(x not in row["c_review"] for x in ["없음", "없읍"]):
            return f"""☕ **주변 추천 카페**\n\n- **{row['c_name']}** (⭐ {row['c_value']})  \n“{row['c_review']}”"""
        else:
            return f"""☕ **주변 추천 카페**\n\n- **{row['c_name']}** (⭐ {row['c_value']})"""

    else:
        grouped = cafes_df.groupby(['c_name', 'c_value'])
        result.append("☕ **주변에 이런 카페들이 있어요** 🌼\n")
        for (name, value), group in grouped:
            reviews = group['c_review'].dropna().unique()
            reviews = [r for r in reviews if all(x not in r for x in ["없음", "없읍"])]
            top_reviews = reviews[:3]

            if top_reviews:
                review_text = "\n".join([f"“{r}”" for r in top_reviews])
                result.append(f"- **{name}** (⭐ {value})  \n{review_text}")
            else:
                result.append(f"- **{name}** (⭐ {value})")

        return "\n\n".join(result)

# 초기 세션 설정
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "system", "content": "당신은 청주 문화유산을 소개하는 감성적이고 공손한 말투의 관광 가이드 챗봇입니다."}
    ]

st.title("🏞️ 청주 문화 관광가이드 🏞️")

# 이전 메시지 출력
for msg in st.session_state.messages[1:]:
    if msg["role"] == "user":
        st.markdown(f"<div style='text-align: right; background-color: #dcf8c6; border-radius: 10px; padding: 8px; margin: 5px 0;'>{msg['content']}</div>", unsafe_allow_html=True)
    elif msg["role"] == "assistant":
        st.markdown(f"<div style='text-align: left; background-color: #ffffff; border-radius: 10px; padding: 8px; margin: 5px 0;'>{msg['content']}</div>", unsafe_allow_html=True)

st.markdown("---")

# 입력 폼 처리
with st.form("chat_form"):
    user_input = st.text_input("지도에서 선택한 관광지들을 여기에 입력해주세요! ( 쉼표(,)로 구분해 주세요. 예: 청주 신선주, 청주 청녕각)")
    submitted = st.form_submit_button("보내기")

if submitted and user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})

    with st.spinner("청주의 아름다움을 정리 중입니다..."):
        places = [p.strip() for p in user_input.split(',') if p.strip()]
        response_blocks = []

        # GPT 서론 생성
        weather_intro = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "당신은 청주 관광을 소개하는 감성적이고 공손한 여행 가이드입니다."},
                {"role": "user", "content": "오늘 청주의 날씨, 추천 복장, 걷기 좋은 시간대, 소소한 여행 팁, 계절 분위기 등을 이모지와 함께 따뜻한 말투로 소개해 주세요. 관광지 소개 전 서론으로 쓸 내용입니다."}
            ]
        ).choices[0].message.content
        response_blocks.append(f"\U0001F324️ {weather_intro}")

        for place in places:
            matched = data[data['t_name'].str.contains(place, na=False)]

            gpt_place_response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "당신은 청주 문화유산을 소개하는 감성적이고 따뜻한 말투의 공손한 관광 가이드입니다. 이모지도 풍부하게 사용하세요."},
                    {"role": "user", "content": f"""
여행자에게 설렘이 느껴지도록, 따뜻하고 공손한 말투로 {place}를 소개해 주세요 ✨  
✔️ 역사적인 배경,  
✔️ 방문 시의 분위기와 계절의 어울림 🍃🌸  
✔️ 인근 포토스팟 📸  
✔️ 여행자에게 추천하는 감성적인 코멘트 🌿  
문단마다 이모지를 활용해 생동감 있게 작성해 주세요. 줄바꿈도 적절히 해 주세요.
"""}
                ]
            ).choices[0].message.content

            if not matched.empty:
                cafes = matched[['c_name', 'c_value', 'c_review']].drop_duplicates()
                cafe_info = format_cafes(cafes)

                t_value = matched['t_value'].dropna().unique()
                score_text = f"\n\n📊 **관광지 평점**: ⭐ {t_value[0]}" if len(t_value) > 0 else ""

                reviews = matched['t_review'].dropna().unique()
                reviews = [r for r in reviews if all(x not in r for x in ["없음", "없읍"])]
                if len(reviews) > 0:
                    top_reviews = list(reviews)[:3]
                    review_text = "\n".join([f"“{r}”" for r in top_reviews])
                    review_block = f"\n\n💬 **방문자 리뷰 중 일부**\n{review_text}"
                else:
                    review_block = ""
            else:
                score_text = ""
                review_block = ""
                cafe_info = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "당신은 청주 지역의 감성적인 관광 가이드입니다. 공손하고 따뜻한 말투로 주변 카페를 추천하세요."},
                        {"role": "user", "content": f"{place} 주변에 어울리는 카페를 2~3곳 추천해 주세요. 이름, 분위기, 어떤 사람에게 잘 어울리는지 등을 감성적으로 설명해 주세요. 이모지와 줄바꿈도 사용해 주세요."}
                    ]
                ).choices[0].message.content

            full_block = f"---\n\n<h2 style='font-size: 24px; font-weight: bold;'>🏛️ {place}</h2>{score_text}\n\n{gpt_place_response}{review_block}\n\n{cafe_info}"
            response_blocks.append(full_block)

        final_response = "\n\n".join(response_blocks)
        st.session_state.messages.append({"role": "assistant", "content": final_response})
