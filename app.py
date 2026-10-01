import base64
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

st.title("📝 영단어 및 문장 자동 채점 (학생별 그룹 채점 모드)")
st.write(
    "스마트폰에서 여러 장의 답안지를 한번에 올려도 학생별로 자동 분류하여 오답노트를 생성합니다."
)

st.sidebar.header("🔑 설정")

# Secrets에 저장된 API 키 확인
if "OPENAI_API_KEY" in st.secrets:
    api_key = st.secrets["OPENAI_API_KEY"]
    st.sidebar.success("✅ API 키가 저장되어 있습니다.")
else:
    api_key = st.sidebar.text_input("OpenAI API Key 입력", type="password")

st.markdown("### 1단계: 교재 정답지 엑셀 파일 업로드")
st.info(
    "💡 엑셀 형식: **1열 = 문항 번호**, **2열 = 정답 단어/문장** (첫 번째 시트)"
)
answer_file = st.file_uploader("정답지 엑셀 (.xlsx) 파일 선택", type=["xlsx"])

st.markdown("### 2단계: 학생 답안지 사진 업로드")
st.caption(
    "📌 권장 파일명: `홍길동_Unit01_1.jpg`, `홍길동_Unit01_2.jpg` (파일명 기반 자동 그룹화)"
)
student_photos = st.file_uploader(
    "답안지 사진 업로드 (다중 선택 가능)",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=True,
)


def compress_and_encode_image(uploaded_file, max_size=1200):
    """이미지 해상도를 최적화하여 GPT-4o 토큰 비용 절감"""
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
    text = unicodedata.normalize("NFC", text)
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


def parse_filename(filename):
    """파일명에서 (학생명, 유닛, 페이지) 추출"""
    name_only = filename.rsplit(".", 1)[0]
    parts = name_only.split("_")
    if len(parts) >= 3:
        return parts[0].strip(), parts[1].strip(), parts[2].strip()
    elif len(parts) == 2:
        return parts[0].strip(), parts[1].strip(), "1"
    else:
        return "미분류_학생", "공통", name_only


# ---------------------------------------------------------
# 업로드된 사진 파일 자동 그룹화 및 미리보기 영역
# ---------------------------------------------------------
student_groups = {}

if student_photos:
    for photo in student_photos:
        s_name, unit, page = parse_filename(photo.name)
        group_key = f"{s_name} ({unit})" if unit != "공통" else s_name

        if group_key not in student_groups:
            student_groups[group_key] = []

        student_groups[group_key].append(
            {"file": photo, "page": page, "filename": photo.name}
        )

    st.success(
        f"총 {len(student_photos)}장의 사진이 {len(student_groups)}개 그룹으로 분류되었습니다."
    )

    # 학생별 그룹 목록 확인 (Expander)
    with st.expander("🔍 업로드된 학생별 사진 목록 확인하기"):
        for g_name, items in student_groups.items():
            st.write(f"👤 **{g_name}** - 총 {len(items)}장")
            cols = st.columns(min(len(items), 3))
            for idx, item in enumerate(items):
                with cols[idx % 3]:
                    img = Image.open(item["file"])
                    st.image(
                        img,
                        caption=f"{item['filename']}",
                        use_column_width=True,
                    )


# ---------------------------------------------------------
# 채점 실행 버튼 영역
# ---------------------------------------------------------
st.markdown("---")
if st.button("🚀 전체 채점 시작하기", type="primary", use_container_width=True):
    if not api_key or not answer_file or not student_photos:
        st.error(
            "API 키, 정답지 엑셀 파일, 학생 답안지 사진을 모두 확인해 주세요."
        )
    else:
        try:
            # 엑셀 데이터 로드
            df_answers = pd.read_excel(answer_file, header=None).dropna(
                how="all"
            )
            answer_dict = dict(
                zip(
                    df_answers.iloc[:, 0].astype(str).str.strip(),
                    df_answers.iloc[:, 1].astype(str).str.strip(),
                )
            )
            formatted_answers = json.dumps(answer_dict, ensure_ascii=False)

            client = OpenAI(api_key=api_key)

            # 학생 그룹별로 순차 채점 수행
            for group_name, items in student_groups.items():
                st.subheader(f"📄 [{group_name}] 채점 진행 중...")

                all_wrong_details = []
                total_correct = 0
                total_q_count = 0

                # 페이지 순서대로 정렬
                sorted_items = sorted(items, key=lambda x: x["page"])

                for item in sorted_items:
                    photo = item["file"]
                    base64_image = compress_and_encode_image(
                        photo, max_size=1200
                    )

                    prompt = f"""
                    당신은 어린 초등학생/중학생의 영단어 및 문장 시험지를 채점하는 따뜻하고 꼼꼼한 전문 채점 선생님입니다. 
                    첨부된 사진에는 두 가지 형태의 문항이 포함되어 있습니다:
                    1. [단어형]: 한글 단어 옆에 학생이 영어 단어를 적는 형태
                    2. [문장/구 빈칸형]: 영어 문장 중간에 밑줄(___)이 그어져 있고, 밑줄 위에 학생이 영단어/문장을 채워 넣은 형태

                    [어린이 필적 판독 및 관대 평가 핵심 규칙]
                    1. 옅은 연필 자국 및 부분 훼손 글자도 흐릿하더라도 구제하여 정답 인정 여부를 판별하세요.
                    2. 글씨가 삐뚤빼뚤하거나 알파벳 획이 뭉개졌더라도 정답 의도가 명확하다면 인정하세요.
                    3. 완전히 검게 덧칠하거나 선을 긋고 새로 적은 경우 최종 답안을 최우선으로 인식하세요.
                    4. 글자가 완전히 없는 빈칸인 경우에만 student_answer에 "(미응답)"으로 작성하세요.

                    [교재 정답지]
                    {formatted_answers}

                    [JSON 응답 형식]
                    {{
                      "student_name": "{group_name}",
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

                    # 채점 정규화 평가
                    for detail in result.get("details", []):
                        total_q_count += 1
                        s_ans = str(detail.get("student_answer", "")).strip()
                        c_ans = str(detail.get("correct_answer", "")).strip()

                        is_correct, reason = evaluate_answer(s_ans, c_ans)
                        if is_correct:
                            total_correct += 1
                        else:
                            all_wrong_details.append({
                                "문항 번호": detail.get("number", ""),
                                "학생 작성 답안 (오답)": s_ans,
                                "교재 정답": c_ans,
                                "파일명": photo.name,
                            })

                # 그룹(학생/유닛 단위) 채점 결과 종합 출력
                wrong_count = total_q_count - total_correct
                st.success(
                    f"✅ **{group_name}** 채점 완료! | 점수: **{total_correct} / {total_q_count}점** (틀린 문항: {wrong_count}개)"
                )

                if all_wrong_details:
                    df_wrong = pd.DataFrame(all_wrong_details)
                    st.dataframe(df_wrong, use_container_width=True)

                    csv_data = df_wrong.to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        label=f"📥 {group_name}_오답노트 다운로드",
                        data=csv_data,
                        file_name=f"오답노트_{group_name}.csv",
                        mime="text/csv",
                        key=f"dl_{group_name}",
                    )
                else:
                    st.balloons()
                    st.info(
                        f"🎉 **{group_name}** 학생은 제출한 시험지의 모든 문제를 맞혔습니다!"
                    )

                st.markdown("---")

        except Exception as e:
            st.error(f"채점 중 오류가 발생했습니다: {e}")
