import streamlit as st
import pandas as pd
from openai import OpenAI
import json
import base64
from PIL import Image
import io
import re
import unicodedata

st.set_page_config(page_title="영단어/문장 자동 채점", page_icon="📝", layout="centered")

st.title("📝 영단어 및 문장 자동 채점 (수정 흔적 판독 보강)")
st.write("지우개 잔상 무시 및 취소선/덧칠 수정 답안 판독 로직이 보강된 GPT-4o 기반 자동 채점 시스템입니다.")

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

def compress_and_encode_image(uploaded_file, max_size=1200):
    """
    이미지 해상도를 1,200px 수준으로 최적화하여 GPT-4o 토큰 비용을 50% 이상 절감하는 함수
    """
    image = Image.open(uploaded_file)
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode('utf-8')

def normalize_text(text: str, remove_punctuation: bool = True) -> str:
    """
    대소문자, 문장부호, 불필요한 연속 공백을 통일하는 파이썬 정규화 함수
    """
    if not text:
        return ""
    text = unicodedata.normalize('NFC', text)
    text = text.lower()
    if remove_punctuation:
        text = re.sub(r"[^\w\s]", "", text)  # 마침표, 쉼표, 아포스트로피 등 문장부호 제거
    text = re.sub(r"\s+", " ", text).strip()  # 연속 공백을 단일 공백으로 압축
    return text

def evaluate_answer(student_ans: str, correct_ans: str) -> tuple[bool, str]:
    """
    학생 답안과 정답을 정규화하여 최종 정오답 및 판정 사유를 반환하는 함수
    """
    norm_student = normalize_text(student_ans)
    norm_correct = normalize_text(correct_ans)
    
    if not norm_student or norm_student == "미응답":
        return False, "미응답 또는 인식 불가"
    
    if norm_student == norm_correct:
        return True, "정답 (대소문자/문장부호/띄어쓰기 예외 적용)"
    else:
        return False, "철자 불일치"

if st.button("🚀 채점 시작하기", type="primary"):
    if not api_key or not answer_file or not student_photos:
        st.error("API 키, 정답지 엑셀 파일, 학생 사진을 모두 확인해주세요.")
    else:
        try:
            # 엑셀 데이터 로드 (첫 번째 시트 기준, 빈 행 제거)
            df_answers = pd.read_excel(answer_file, header=None)
            df_answers = df_answers.dropna(how='all')
            
            answer_dict = dict(zip(df_answers.iloc[:, 0].astype(str).str.strip(), df_answers.iloc[:, 1].astype(str).str.strip()))
            formatted_answers = json.dumps(answer_dict, ensure_ascii=False)
            
            client = OpenAI(api_key=api_key)
            
            for photo in student_photos:
                st.markdown(f"--- 📄 **[진행 중]** {photo.name} 이미지 최적화 및 채점 중...")
                
                # 이미지 압축 (비용 절감)
                base64_image = compress_and_encode_image(photo, max_size=1200)
                
                # 지우개 잔상 및 취소선/덧칠 예외처리가 강화된 정밀 프롬프트
                prompt = f'''
                당신은 꼼꼼한 영어 시험지 전문 채점 보조원입니다. 
                첨부된 사진에는 두 가지 형태의 문항이 포함되어 있습니다:
                1. [단어형]: 한글 단어 옆에 학생이 영어 단어를 적는 형태
                2. [문장/구 빈칸형]: 영어 문장 중간에 밑줄(___)이 그어져 있고, 밑줄 위에 학생이 영단어/문장을 채워 넣은 형태

                [이미지 판독 필수 규칙]
                - 문장 빈칸형 문제는 **인쇄된 문장이 아닌 '밑줄 바로 위'에 학생이 손글씨로 적은 글자만 정확히 추출**하세요.
                
                [수정 흔적 판독 규칙 - 매우 중요!]
                1. **지우개 자국(연한 잔상) 무시**:
                   - 지우개로 지워서 옅고 번지게 남은 연필 자국은 지워진 답안으로 판단하고 무시하세요.
                   - 그 위에나 주변에 **가장 짙고 선명하게 새로 쓴 필적만 최종 답안으로 인식**하세요.
                2. **취소선 및 덧칠/덮어쓰기 무시**:
                   - 연필로 여러 번 선을 긋거나, 색칠하듯 덮어써서 검게 칠해놓은 단어는 취소된 답안으로 처리하세요.
                   - 이렇게 **취소/덧칠해진 단어 옆이나 위/아래에 새로 적어놓은 최종 단어만 식별**하여 student_answer로 추출하세요.
                3. 학생 답안이 완전히 빈칸이거나 지우개로 지워져 알아볼 수 없으면 student_answer에 "(미응답)"으로 적어주세요.

                [교재 정답지]
                {formatted_answers}

                [JSON 응답 형식]
                {{
                  "student_name": "학생 이름",
                  "total_questions": 전체문항수,
                  "details": [
                    {{
                      "number": "1",
                      "student_answer": "학생이 최종적으로 수정한 답안",
                      "correct_answer": "교재 정답"
                    }}
                  ]
                }}
                '''
                
                # GPT-4o 호출 (고화질 디테일 옵션 적용)
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
                
                # 파이썬 정규화 코드를 통한 2차 검증 및 채점 실행
                final_details = []
                correct_count = 0
                
                for item in result['details']:
                    s_ans = str(item.get('student_answer', '')).strip()
                    c_ans = str(item.get('correct_answer', '')).strip()
                    
                    is_correct, reason = evaluate_answer(s_ans, c_ans)
                    if is_correct:
                        correct_count += 1
                        
                    final_details.append({
                        "문항 번호": item.get('number', ''),
                        "학생 답안": s_ans,
                        "교재 정답": c_ans,
                        "정답 여부": "⭕ 정답" if is_correct else "❌ 오답",
                        "판정 비고": reason
                    })
                
                total_q = len(final_details)
                student_name = result.get('student_name', '미상')
                
                st.success(f"완료! 학생명: **{student_name}** | 점수: **{correct_count} / {total_q}점**")
                
                df_result = pd.DataFrame(final_details)
                st.dataframe(df_result, use_container_width=True)
                
                # CSV 결과 다운로드 버튼
                csv_data = df_result.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label=f"📥 {student_name}_채점결과 다운로드",
                    data=csv_data,
                    file_name=f"채점결과_{student_name}.csv",
                    mime="text/csv",
                    key=photo.name
                )
        except Exception as e:
            st.error(f"채점 중 오류가 발생했습니다: {e}")
