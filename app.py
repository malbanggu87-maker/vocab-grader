import base64
import difflib
import io
import json
import re
import unicodedata
from PIL import Image
from openai import OpenAI
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="영단어/문장 오답 전용 채점", page_icon="📝", layout="centered"
)

st.title("📝 제이슨의 구원방주")
st.write("Made by Jason")

st.sidebar.header("🔑 설정")

# Secrets에 저장된 API 키 확인
if "OPENAI_API_KEY" in st.secrets:
    api_key = st.secrets["OPENAI_API_KEY"]
    st.sidebar.success("✅ API 키가 저장되어 있습니다.")
else:
    api_key = st.sidebar.text_input("OpenAI API Key 입력", type="password")

st.markdown("### 1단계: 교재 정답지 엑셀 파일 업로드")
st.info("💡 엑셀 형식: **1열 = 문항 번호**, **2열 = 정답 단어/문장** (첫 번째 시트)")

# 모바일 브라우저 호환성을 위한 확장자 및 MIME-Type 허용
answer_file = st.file_uploader(
    "정답지 엑셀 (.xlsx, .xls) 파일 선택",
    type=["xlsx", "xls", "csv"],
    help="갤럭시 등 모바일 단말기에서는 내장 파일 관리자를 통해 선택해주세요."
)

st.markdown("### 2단계: 답안지 사진 업로드")
st.caption(
    "📌 파일명 규칙 필요 없음: 제출된 시험지 여러 장을 그대로 다중 선택하여 업로드하세요."
)
student_photos = st.file_uploader(
    "답안지 사진 업로드 (다중 선택 가능)",
    type=["jpg", "jpeg", "png", "heic", "webp"],
    accept_multiple_files=True,
)


def compress_and_encode_image(uploaded_file, max_size=1200):
    """이미지 해상도를 최적화하여 GPT-4o 토큰 비용 절감"""
    uploaded_file.seek(0)
    image = Image.open(uploaded_file)
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def normalize_text(text: str, remove_punctuation: bool = True) -> str:
    """텍스트 정규화 (대소문자, 공백, 문장부호)"""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = text.lower()
    if remove_punctuation:
        text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def evaluate_answer(student_ans: str, correct_ans: str) -> tuple[bool, str]:
    """정오답 판정 함수"""
    norm_student = normalize_text(student_ans)
    norm_correct = normalize_text(correct_ans)

    if not norm_student or norm_student in ["미응답", "(미응답)"]:
        return False, "미응답 또는 인식 불가"

    if norm_student == norm_correct:
        return True, "정답"
    else:
        return False, "철자 불일치"


def generate_diff_html(student_ans: str, correct_ans: str) -> str:
    """
    학생 답안과 정답을 글자 단위로 비교하여 HTML 스트링을 생성합니다.
    - 틀린 철자/잘못 적은 단어: 빨간색 알파벳
    - 빠뜨리거나 미응답인 부분: 빠진 철자 수만큼 빨간색 물음표(?)
    """
    if not student_ans or student_ans in ["미응답", "(미응답)"]:
        missing_length = max(len(correct_ans.strip()), 1)
        return f'{"?" * missing_length}'

    diff = list(difflib.ndiff(correct_ans, student_ans))
    html_result = ""
    i = 0

    while i < len(diff):
        code, val = diff[i][0], diff[i][2]

        if code == ' ':
            html_result += val
            i += 1
        elif code == '-':
            missing_count = 0
            while i < len(diff) and diff[i][0] == '-':
                missing_count += 1
                i += 1
            html_result += f'{"?" * missing_count}'
        elif code == '+':
            wrong_chars = ""
            while i < len(diff) and diff[i][0] == '+':
                wrong_chars += diff[i][2]
                i += 1
            html_result += f'{wrong_chars}'
        else:
            i += 1

    return html_result


# ---------------------------------------------------------
# 업로드된 사진 미리보기 영역 (파일명 규칙 없음)
# ---------------------------------------------------------
if student_photos:
    st.success(
        f"총 {len(student_photos)}장의 사진이 성공적으로 업로드되었습니다."
    )

    with st.expander("🔍 업로드된 원본 사진 목록 및 미리보기"):
        cols = st.columns(3)
        for idx, photo in enumerate(student_photos):
            with cols[idx % 3]:
                photo.seek(0)
                img = Image.open(photo)
                st.image(
                    img,
                    caption=f"파일: {photo.name}",
                    use_container_width=True,
                )


# ---------------------------------------------------------
# 채점 실행 버튼 영역
# ---------------------------------------------------------
st.markdown("---")
if st.button("🚀 채점을 조지십시요", type="primary", use_container_width=True):
    if not api_key or not answer_file or not student_photos:
        st.error(
            "API 키, 정답지 엑셀 파일, 학생 답안지 사진을 모두 확인해 주세요."
        )
    else:
        try:
            # 엑셀/CSV 데이터 안전 로드
            answer_file.seek(0)
            if answer_file.name.endswith(".csv"):
                df_answers = pd.read_csv(answer_file, header=None).dropna(how="all")
            else:
                df_answers = pd.read_excel(answer_file, header=None, engine="openpyxl").dropna(how="all")

            # 정답 dictionary 생성 (소수점 번호 방지 .replace(r'\.0$', ''))
            q_nums = df_answers.iloc[:, 0].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
            q_ans = df_answers.iloc[:, 1].astype(str).str.strip()
            
            answer_dict = dict(zip(q_nums, q_ans))
            formatted_answers = json.dumps(answer_dict, ensure_ascii=False)

            client = OpenAI(api_key=api_key)

            student_results_map = {}

            for idx, photo in enumerate(student_photos):
                st.markdown(
                    f"--- 📄 **[진행 중 ({idx+1}/{len(student_photos)})]** `{photo.name}` 분석 및 학생 식별 중..."
                )

                base64_image = compress_and_encode_image(photo, max_size=1200)

                prompt = f"""
                당신은 어린 초등학생/중학생의 영단어 및 문장 시험지를 채점하는 따뜻하고 꼼꼼한 전문 채점 선생님입니다. 
                1. **[학생 이름 식별]**: 이미지 상단이나 표기된 영역에서 **학생의 이름**을 정확히 찾아내어 'student_name' 필드에 적어주세요. (만약 이름이 적혀있지 않거나 판독이 불가능하면 "미확인학생"으로 적어주세요.)
                2. 첨부된 사진에는 두 가지 형태의 문항이 포함되어 있습니다:
                   - [단어형]: 한글 단어 옆에 학생이 영어 단어를 적는 형태
                   - [문장/구 빈칸형]: 영어 문장 중간에 밑줄(___)이 그어져 있고, 밑줄 위에 학생이 영단어/문장을 채워 넣은 형태

                [어린이 필적 판독 및 관대 평가 핵심 규칙]
                - 옅은 연필 자국 및 부분 훼손 글자도 흐릿하더라도 구제하여 정답 인정 여부를 판별하세요.
                - 글씨가 삐뚤빼뚤하거나 알파벳 획이 뭉개졌더라도 정답 의도가 명확하다면 인정하세요.
                - 완전히 검게 덧칠하거나 선을 긋고 새로 적은 경우 최종 답안을 최우선으로 인식하세요.
                - 글자가 완전히 없는 빈칸인 경우에만 student_answer에 "(미응답)"으로 작성하세요.

                [교재 정답지]
                {formatted_answers}

                [JSON 응답 형식]
                {{
                  "student_name": "시험지에 적힌 학생 이름",
                  "details": [
                    {{
                      "number": "문항번호",
                      "student_answer": "학생이 작성한 답",
                      "correct_answer": "교재 정답"
                    }}
                  ]
                }}
                """

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
                                    "detail": "high",
                                },
                            },
                        ],
                    }],
                    response_format={"type": "json_object"},
                )

                result = json.loads(response.choices[0].message.content)
                detected_name = result.get("student_name", "미확인학생").strip()

                if detected_name not in student_results_map:
                    student_results_map[detected_name] = []

                for detail in result.get("details", []):
                    s_ans = str(detail.get("student_answer", "")).strip()
                    c_ans = str(detail.get("correct_answer", "")).strip()

                    is_correct, reason = evaluate_answer(s_ans, c_ans)

                    visualized_html = (
                        generate_diff_html(s_ans, c_ans)
                        if not is_correct
                        else s_ans
                    )

                    student_results_map[detected_name].append({
                        "문항 번호": str(detail.get("number", "")).replace(".0", ""),
                        "학생 작성 답안": s_ans,
                        "오답 분석 (대조)": visualized_html,
                        "교재 정답": c_ans,
                        "정오답": is_correct,
                        "파일명": photo.name,
                    })

            # ---------------------------------------------------------
            # 학생별로 데이터 취합 및 최종 리포트/오답노트 출력
            # ---------------------------------------------------------
            st.markdown("---")
            st.subheader("📊 학생별 최종 채점 결과 종합")

            for s_name, records in student_results_map.items():
                total_q_count = len(records)
                total_correct = sum(1 for r in records if r["정오답"])
                wrong_count = total_q_count - total_correct

                wrong_details = [
                    {
                        "문항 번호": r["문항 번호"],
                        "오답 분석 (대조)": r["오답 분석 (대조)"],
                        "교재 정답": r["교재 정답"],
                        "원본 학생 답안": r["학생 작성 답안"],
                        "파일명": r["파일명"],
                    }
                    for r in records
                    if not r["정오답"]
                ]

                st.markdown(f"### 👤 학생: **{s_name}**")
                st.info(
                    f"점수: **{total_correct} / {total_q_count}점** (틀린 문항: {wrong_count}개)"
                )

                if wrong_details:
                    df_wrong = pd.DataFrame(wrong_details)
                    
                    df_display = df_wrong[["문항 번호", "오답 분석 (대조)", "교재 정답", "파일명"]]
                    st.write(
                        df_display.to_html(escape=False, index=False),
                        unsafe_allow_html=True
                    )
                    st.write("")

                    clean_wrong_details = []
                    for w in wrong_details:
                        clean_wrong_details.append({
                            "문항 번호": w["문항 번호"],
                            "학생 작성 답안": w["원본 학생 답안"],
                            "교재 정답": w["교재 정답"],
                            "파일명": w["파일명"],
                        })
                    df_csv = pd.DataFrame(clean_wrong_details)
                    csv_data = df_csv.to_csv(index=False).encode("utf-8-sig")

                    st.download_button(
                        label=f"📥 {s_name}_오답노트 다운로드 (CSV)",
                        data=csv_data,
                        file_name=f"오답노트_{s_name}.csv",
                        mime="text/csv",
                        key=f"dl_{s_name}",
                    )
                else:
                    st.balloons()
                    st.success(
                        f"🎉 **{s_name}** 학생은 제출한 모든 문항을 맞혔습니다!"
                    )

                st.markdown("---")

        except Exception as e:
            st.error(f"채점 중 오류가 발생했습니다: {e}")
