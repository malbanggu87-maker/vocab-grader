import streamlit as st
import pandas as pd
from openai import OpenAI
from PIL import Image
import json

# 페이지 기본 설정
st.set_page_config(page_title="영단어 시험 자동 채점", page_icon="📝", layout="centered")

st.title("📝 영단어 시험 자동 채점 프로그램")
st.write("갤럭시 S25로 찍은 답안지 사진과 엑셀 정답지를 업로드하면, 교재 단어 기준에 맞춰 정교하게 채점합니다.")

# 1. API 키 입력 (사이드바 또는 메인 화면)
st.sidebar.header("🔑 설정")
api_key = st.sidebar.text_input("OpenAI API Key 입력", type="password")

# 2. 교재 정답지 엑셀 파일 업로드
st.markdown("### 1단계: 교재 정답지 엑셀 파일 업로드")
st.info("💡 엑셀 파일 형식: **1열 = 문항 번호**, **2열 = 정답 단어** (첫 번째 시트 기준)")
answer_file = st.file_uploader("정답지 엑셀 (.xlsx) 파일 선택", type=["xlsx"])

# 3. 학생 답안지 사진 업로드 (다중 선택 가능)
st.markdown("### 2단계: 학생 답안지 사진 업로드")
student_photos = st.file_uploader(
    "갤럭시 S25로 촬영한 답안지 사진 (여러 장 한 번에 선택 가능)", 
    type=["jpg", "jpeg", "png"], 
    accept_multiple_files=True
)

# 4. 채점 실행 버튼
if st.button("🚀 채점 시작하기", type="primary"):
    if not api_key:
        st.error("사이드바에 OpenAI API Key를 입력해주세요.")
    elif not answer_file:
        st.error("교재 정답지 엑셀 파일을 업로드해주세요.")
    elif not student_photos:
        st.error("채점할 학생 답안지 사진을 최소 1장 이상 업로드해주세요.")
    else:
        try:
            # 엑셀 정답지 데이터 로드
            df_answers = pd.read_excel(answer_file)
            answer_dict = dict(zip(df_answers.iloc[:, 0].astype(str), df_answers.iloc[:, 1].astype(str).str.strip()))
            formatted_answers = json.dumps(answer_dict, ensure_ascii=False, indent=2)
            
            client = OpenAI(api_key=api_key)
            
            # 업로드된 사진마다 반복 채점 수행
            for photo in student_photos:
                st.markdown(f"--- 📄 **[진행 중]** {photo.name} 채점 중...")
                
                # 이미지 읽기
                image = Image.open(photo)
                
                # OpenAI Vision API 프롬프트 구성
                prompt = f"""
                당신은 영어학원의 엄격한 단어 시험 채점 보조원입니다.
                제시된 학생 답안지 사진을 읽고, 아래 교재 정답지와 대조하여 채점하세요.

                [교재 정답지 데이터]
                {formatted_answers}

                [채점 지침]
                1. 사진에서 문항 번호와 학생이 손으로 쓴 영단어를 정확히 인식하세요.
                2. 학생이 쓴 단어가 [교재 정답지 데이터]의 단어와 철자(Spelling)가 완벽히 일치해야만 정답(true) 처리합니다.
                3. 뜻이 같거나 유의어(Synonym)라도 교재 정답지에 기재된 단어와 다르면 무조건 오답(false) 처리하세요.
                4. 대소문자는 구분하지 않습니다.
                5. 학생 이름이 상단에 적혀있다면 인식하고, 없으면 "미확인"으로 표기하세요.

                [응답 포맷 (JSON 전용)]
                {{
                  "student_name": "학생 이름",
                  "total_questions": 전체문항수,
                  "score": 맞은개수,
                  "details": [
                    {{
                      "number": "1",
                      "student_answer": "학생이 작성한 단어",
                      "correct_answer": "교재 정답",
                      "is_correct": true
                    }}
                  ]
                }}
                """
                
                # OpenAI API 호출 (GPT-4o)
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": photo,  # Streamlit 업로드 파일 객체 또는 바이트 처리
                                        "detail": "high"
                                    }
                                }
                            ]
                        }
                    ],
                    response_format={"type": "json_object"}
                )
                
                # 결과 파싱
                result = json.loads(response.choices[0].message.content)
                
                # 화면 출력
                st.success(f"완료! 학생명: **{result['student_name']}** | 점수: **{result['score']} / {result['total_questions']}점**")
                
                # 상세 채점 표 표시
                df_result = pd.DataFrame(result['details'])
                # 보기 편하게 컬럼 이름 변경
                df_result.columns = ["문항 번호", "학생 답안", "교재 정답", "정답 여부"]
                st.dataframe(df_result, use_container_width=True)
                
                # 엑셀 다운로드 버튼 제공
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
