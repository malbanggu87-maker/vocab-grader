import streamlit as st
import pandas as pd
from openai import OpenAI
import json
import base64
from PIL import Image
import io
import re
import unicodedata

st.set_page_config(page_title="영단어/문장 오답 전용 채점", page_icon="📝", layout="centered")

st.title("📝 영단어 및 문장 자동 채점 (오답 집중 모드)")
st.write("학생이 틀린 문항만 직관적으로 추출하여 오답과 교재 정답을 한눈에 비교할 수 있도록 보여줍니다.")

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
        return True, "정답"
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
                st.markdown(f"--- 📄 **[진행 중]** {photo.name} 분석 및 오답 추출 중...")
                
                # 이미지 압축 (비용 절감)
                base64_image = compress_and_encode_image(photo, max_size=1200)
                
                # 정밀 프롬프트
                prompt = f'''
                당신은 어린 초등학생/중학생의 영단어 및 문장 시험지를 채점하는 따뜻하고 꼼꼼한 전문 채점 선생님입니다. 
                첨부된 사진에는 두 가지 형태의 문항이 포함되어 있습니다:
                1. [단어형]: 한글 단어 옆에 학생이 영어 단어를 적는 형태
                2. [문장/구 빈칸형]: 영어 문장 중간에 밑줄(___)이 그어져 있고, 밑줄 위에 학생이 영단어/문장을 채워 넣은 형태

                [어린이 필적 판독 및 관대 평가 핵심 규칙]
                1. **옅은 연필 자국 및 부분 훼손 글자 구제**:
                   - 어린 학생들이 힘없이 써서 매우 옅거나 흐릿하게 보이는 글자도 주의 깊게 살펴 읽어내세요.
                   - 다른 빈칸을 지우다가 함께 연하게 지워진 필적이라도 정답 단어의 형태가 남아있다면 학생이 적은 답으로 인정하세요.
                2. **악필 및 뭉개진 획의 작성 의도 감안**:
                   - 글씨가 삐뚤빼뚤하거나 알파벳 획이 뭉개졌더라도(예: u와 v, a와 o, n과 r 등), 정답 단어와 문맥상 학생이 해당 철자를 쓰려고 한 의도가 명확하다면 가능한 정답 철자로 인정해 주세요.
                3. **수정 흔적 구분**:
                   - 완전히 검게 덧칠하거나 선을 긋고 그 옆/위/아래에 새로 적은 답이 있다면 최종 수정 답안을 최우선으로 인식하세요.
                4. **미응답 기준**:
                   - 글자가 완전히 존재하지 않는 빈칸이거나 전혀 읽을 수 없는 무작위 낙서인 경우에만 student_answer에 "(미응답)"으로 작성하세요.

                [교재 정답지]
                {formatted_answers}

                [JSON 응답 형식]
                {{
                  "student_name": "학생 이름",
                  "total_questions": 전체문항수,
                  "details": [
                    {{
                      "number": "1",
                      "student_answer": "학생이 최종적으로 적거나 의도한 답안",
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
                
                # 파이썬 정규화 코드를 통한 오답만 추출 및 정리
                wrong_details = []
                correct_count = 0
                total_q = len(result['details'])
                
                for item in result['details']:
                    s_ans = str(item.get('student_answer', '')).strip()
                    c_ans = str(item.get('correct_answer', '')).strip()
                    
                    is_correct, reason = evaluate_answer(s_ans, c_ans)
                    if is_correct:
                        correct_count += 1
                    else:
                        # 오답 문항만 표출 리스트에 담기
                        wrong_details.append({
                            "문항 번호": item.get('number', ''),
                            "학생 작성 답안 (오답)": s_ans,
                            "교재 정답": c_ans
                        })
                
                student_name = result.get('student_name', '미상')
                wrong_count = total_q - correct_count
                
                st.success(f"완료! 학생명: **{student_name}** | 점수: **{correct_count} / {total_q}점** (틀린 문항: {wrong_count}개)")
                
                # 틀린 문항 유무에 따른 간결 표출
                if wrong_details:
                    st.subheader("❌ 틀린 문항 비교 목록")
                    df_wrong = pd.DataFrame(wrong_details)
                    st.dataframe(df_wrong, use_container_width=True)
                    
                    # CSV 결과 다운로드 버튼 (오답 전용)
                    csv_data = df_wrong.to_csv(index=False).encode('utf-8-sig')
                    st.download_button(
                        label=f"📥 {student_name}_오답노트 다운로드",
                        data=csv_data,
                        file_name=f"오답노트_{student_name}.csv",
                        mime="text/csv",
                        key=photo.name
                    )
                else:
                    st.balloons()
                    st.info("🎉 **모든 문항을 맞혔습니다! 틀린 오답이 없습니다.**")

        except Exception as e:
            st.error(f"채점 중 오류가 발생했습니다: {e}")
