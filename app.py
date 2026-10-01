import streamlit as st
import pandas as pd
from openai import OpenAI
import json
import base64

st.set_page_config(page_title="영단어 시험 자동 채점", page_icon="📝", layout="centered")

st.title("📝 Grit English 자동채점 프로그램")
st.write("시험지 사진과 엑셀 정답지를 업로드하면 교재 단어 기준에 맞춰 채점합니다.")

st.sidebar.header("🔑 설정")

# Secrets에 저장된 API 키가 있으면 자동으로 사용, 없으면 입력창 표시
if "OPENAI_API_KEY" in st.secrets:
    api_key = st.secrets["OPENAI_API_KEY"]
    st.sidebar.success("✅ API 키가 저장되어 있습니다.")
else:
    api_key = st.sidebar.text_input("OpenAI API Key 입력", type="password")

st.markdown("### 1단계: 교재 정답지 엑셀 파일 업로드")
st.info("💡 엑셀 형식: **1열 = 문항 번호**, **2열 = 정답 단어/문장** (첫 번째 시트)")
answer_file = st.file_uploader("정답지 엑셀 (.xlsx) 파일 선택", type=["xlsx"])

st.markdown("### 2단계: 학생 답안지 사진 업로드")
student_photos = st.file_uploader(
    "답안지 사진 (다중 선택 가능)", 
    type=["jpg", "jpeg", "png"], 
    accept_multiple_files=True
)

if st.button("🚀 채점 시작하기", type="primary"):
    if not api_key or not answer_file or not student_photos:
        st.error("API 키, 정답지 엑셀 파일, 학생 사진을 모두 확인해주세요.")
    else:
        try:
            # 엑셀 데이터 로드 및 간결한 형태로 변환 (토큰 절감)
            df_answers = pd.read_excel(answer_file)
            answer_dict = dict(zip(df_answers.iloc[:, 0].astype(str), df_answers.iloc[:, 1].astype(str).str.strip()))
            formatted_answers = json.dumps(answer_dict, ensure_ascii=False)
            
            client = OpenAI(api_key=api_key)
            
            for photo in student_photos:
                st.markdown(f"--- 📄 **[진행 중]** {photo.name} 채점 중...")
                
                # 이미지 Base64 변환
                bytes_data = photo.getvalue()
                base64_image = base64.b64encode(bytes_data).decode('utf-8')
                
                # 토큰 소모를 줄인 경량화 프롬프트
                prompt = f'''
                영단어 시험 채점 보조원입니다. 사진의 답안을 읽고 교재 정답지와 대조하여 채점하세요.
                
                [교재 정답지]
                {formatted_answers}
                
                [채점 규칙]
                1. 정답지와 철자가 완벽히 일치해야만 true.
                2. 대소문자는 구분하지 않음.
                
                [JSON 응답 형식]
                {{
                  "student_name": "학생 이름",
                  "total_questions": 전체문항수,
                  "score": 맞은개수,
                  "details": [
                    {{
                      "number": "1",
                      "student_answer": "학생 답",
                      "correct_answer": "정답",
                      "is_correct": true
                    }}
                  ]
                }}
                '''
                
                # 비용 절감 적용: gpt-4o-mini 모델 및 detail: "low" 설정
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                    "detail": "low"
                                }
                            }
                        ]
                    }],
                    response_format={"type": "json_object"}
                )
                
                result = json.loads(response.choices[0].message.content)
                st.success(f"완료! 학생명: **{result['student_name']}** | 점수: **{result['score']} / {result['total_questions']}점**")
                
                df_result = pd.DataFrame(result['details'])
                df_result.columns = ["문항 번호", "학생 답안", "교재 정답", "정답 여부"]
                st.dataframe(df_result, use_container_width=True)
                
                csv_data = df_result.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label=f"📥 {result['student_name']}_채점결과 다운로드",
                    data=csv_data,
                    file_name=f"채점결과_{result['student_name']}.csv",
                    mime="text/csv",
                    key=photo.name
                )
        except Exception as e:
            st.error(f"채점 중 오류가 발생했습니다: {e}")
