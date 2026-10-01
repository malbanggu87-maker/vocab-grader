import streamlit as st
import pandas as pd
from openai import OpenAI
from PIL import Image
import json
import base64
import io

st.set_page_config(page_title="영단어 시험 자동 채점", page_icon="📝", layout="centered")

st.title("📝 영단어 시험 자동 채점 프로그램")
st.write("갤럭시 S25로 찍은 답안지 사진과 엑셀 정답지를 업로드하면 교재 단어 기준에 맞춰 채점합니다.")

st.sidebar.header("🔑 설정")
api_key = st.sidebar.text_input("OpenAI API Key 입력", type="password")

st.markdown("### 1단계: 교재 정답지 엑셀 파일 업로드")
st.info("💡 엑셀 형식: **1열 = 문항 번호**, **2열 = 정답 단어/문장** (첫 번째 시트)")
answer_file = st.file_uploader("정답지 엑셀 (.xlsx) 파일 선택", type=["xlsx"])

st.markdown("### 2단계: 학생 답안지 사진 업로드")
student_photos = st.file_uploader(
    "갤럭시 S25로 촬영한 답안지 사진 (다중 선택 가능)", 
    type=["jpg", "jpeg", "png"], 
    accept_multiple_files=True
)

if st.button("🚀 채점 시작하기", type="primary"):
    if not api_key or not answer_file or not student_photos:
        st.error("API 키, 정답지 엑셀 파일, 학생 사진을 모두 확인해주세요.")
    else:
        try:
            # 엑셀 데이터 로드
            df_answers = pd.read_excel(answer_file)
            answer_dict = dict(zip(df_answers.iloc[:, 0].astype(str), df_answers.iloc[:, 1].astype(str).str.strip()))
            formatted_answers = json.dumps(answer_dict, ensure_ascii=False, indent=2)
            
            client = OpenAI(api_key=api_key)
            
            for photo in student_photos:
                st.markdown(f"--- 📄 **[진행 중]** {photo.name} 채점 중...")
                
                # 이미지 파일을 OpenAI가 읽을 수 있는 base64 형식으로 변환 (에러 해결 핵심 부분)
                bytes_data = photo.getvalue()
                base64_image = base64.b64encode(bytes_data).decode('utf-8')
                
                prompt = f'''
                당신은 영어학원의 엄격한 단어 시험 채점 보조원입니다.
                학생 답안지 사진을 읽고 교재 정답지와 대조하여 엄격하게 채점하세요.
                
                [교재 정답지]
                {formatted_answers}
                
                [채점 지침]
                1. 문항 번호와 손글씨 영단어/문장을 정확히 인식하세요.
                2. 정답지의 철자와 완벽히 일치해야만 정답(true) 처리합니다. 뜻이 같아도 교재 단어가 아니면 오답(false) 처리합니다.
                3. 대소문자는 구분하지 않습니다.
                
                [응답 포맷 (JSON 전용)]
                {{
                  "student_name": "학생 이름",
                  "total_questions": 전체문항수,
                  "score": 맞은개수,
                  "details": [
                    {{
                      "number": "1",
                      "student_answer": "학생 단어",
                      "correct_answer": "교재 정답",
                      "is_correct": true
                    }}
                  ]
                }}
                '''
                
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                    "detail": "high"
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
