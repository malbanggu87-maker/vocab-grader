import streamlit as st
import pandas as pd
from openai import OpenAI
import json
import base64

st.set_page_config(page_title="영단어/문장 자동 채점", page_icon="📝", layout="centered")

st.title("📝 영단어 및 빈칸 채우기 자동 채점 프로그램")
st.write("갤럭시 S25로 찍은 답안지 사진과 엑셀 정답지를 업로드하면 교재 단어/문장 기준에 맞춰 정밀 채점합니다.")

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
    "갤럭시 S25로 촬영한 답안지 사진 (다중 선택 가능)", 
    type=["jpg", "jpeg", "png"], 
    accept_multiple_files=True
)

if st.button("🚀 채점 시작하기", type="primary"):
    if not api_key or not answer_file or not student_photos:
        st.error("API 키, 정답지 엑셀 파일, 학생 사진을 모두 확인해주세요.")
    else:
        try:
            # 엑셀 데이터 안전하게 로드 (제목 행 무시 및 유연한 파싱)
            df_answers = pd.read_excel(answer_file, header=None)
            df_answers = df_answers.dropna(how='all') # 빈 행 제거
            
            # 첫 번째 열을 문항 번호, 두 번째 열을 정답으로 지정
            answer_dict = dict(zip(df_answers.iloc[:, 0].astype(str).str.strip(), df_answers.iloc[:, 1].astype(str).str.strip()))
            formatted_answers = json.dumps(answer_dict, ensure_ascii=False)
            
            client = OpenAI(api_key=api_key)
            
            for photo in student_photos:
                st.markdown(f"--- 📄 **[진행 중]** {photo.name} 채점 중...")
                
                # 이미지 Base64 변환
                bytes_data = photo.getvalue()
                base64_image = base64.b64encode(bytes_data).decode('utf-8')
                
                # 밑줄 인식 및 유형별 정밀 채점 프롬프트
                prompt = f'''
                당신은 꼼꼼한 영어 시험지 전문 채점 보조원입니다. 
                첨부된 사진에는 두 가지 형태의 문항이 포함되어 있을 수 있습니다:
                1. [단어형]: 한글 단어 옆에 학생이 영어 단어를 적는 형태
                2. [문장 빈칸형]: 영어 문장 중간중간 밑줄(___)이 그어져 있고, 밑줄 위에 학생이 영단어를 채워 넣은 형태

                [이미지 판독 필수 규칙]
                - 문장 빈칸형 문제의 경우, **인쇄된 문장 텍스트가 아닌 '밑줄 바로 위'에 학생이 손글씨로 적은 글자만 정확히 인식**하세요.
                - 밑줄 아래나 다른 인쇄된 텍스트와 헷갈리지 않도록 visual alignment를 주의 깊게 확인하세요.
                - 손글씨 알파벳 하나하나의 철자(spelling)를 선명하고 주의 깊게 판독하세요.

                [교재 정답지]
                {formatted_answers}

                [채점 규칙]
                1. 정답지와 학생이 밑줄 위에 적은 답안의 철자가 완벽히 일치해야만 is_correct를 true로 설정하세요. (1글자라도 틀리면 false)
                2. 대소문자는 구분하지 않습니다.
                3. 학생 답안이 빈칸이거나 알아볼 수 없으면 student_answer에 "(미응답)"으로 작성하고 false 처리하세요.

                [JSON 응답 형식]
                {{
                  "student_name": "학생 이름",
                  "total_questions": 전체문항수,
                  "score": 맞은개수,
                  "details": [
                    {{
                      "number": "1",
                      "student_answer": "학생이 밑줄 위에 적은 답",
                      "correct_answer": "정답지 상의 정답",
                      "is_correct": true
                    }}
                  ]
                }}
                '''
                
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
