import base64
import io
import json
import re
import unicodedata
from PIL import Image, ImageOps
from openai import OpenAI
import pandas as pd
from pydantic import BaseModel, Field
import streamlit as st

st.set_page_config(
    page_title="영단어/문장 오답 전용 채점", page_icon="📝", layout="centered"
)

st.title("📝 Grit English 채점실행 명령")
st.write("Made by Jason")

st.sidebar.header("🔑 설정")

if "OPENAI_API_KEY" in st.secrets:
    api_key = st.secrets["OPENAI_API_KEY"]
    st.sidebar.success("✅ API 키가 저장되어 있습니다.")
else:
    api_key = st.sidebar.text_input("OpenAI API Key 입력", type="password")

selected_model = st.sidebar.selectbox(
    "🤖 사용할 OpenAI 모델 선택",
    ["gpt-4o", "gpt-4o-mini"],
    index=0,
    help="gpt-4o는 손글씨 및 철자 정밀 검증에 가장 우수한 성능을 보입니다.",
)


def extract_key_number(filename: str) -> str:
    """파일명에서 끝쪽 숫자 2자리를 추출 (숫자 1자리만 있는 경우 01 형태로 정규화)"""
    name_without_ext = re.sub(r"\.[^.]+$", "", filename)
    numbers = re.findall(r"\d+", name_without_ext)
    if numbers:
        last_num = numbers[-1]
        return last_num.zfill(2)[-2:]
    return ""


def normalize_q_num(q_str: str) -> str:
    """문항 번호 정규화 (예: '1.', '1번', '01' -> '1')"""
    if not q_str:
        return ""
    q_clean = str(q_str).strip()
    q_clean = re.sub(r"\.0$", "", q_clean)
    q_clean = re.sub(r"[^\d]", "", q_clean)
    if q_clean.isdigit():
        return str(int(q_clean))
    return str(q_str).strip()


class QuestionResult(BaseModel):
    number: str = Field(
        description="시험지에 인쇄된 정확한 문항 번호 (예: '1', '17', '19', '25')"
    )
    spelled_out_letters: str = Field(
        description=(
            "학생이 해당 문항 위치에 손글씨로 쓴 글자를 눈에 보이는 그대로 하이픈(-)으로 나누어 적으세요. "
            "예시: 't-r-e-a-s-u-r-e' 또는 'i-m-p-o-s-e-r'\n"
            "⚠️ [절대 주의 - 자체 보정 및 추측 왜곡 엄금]:\n"
            "1. 교재 정답이 'composer'여도 학생이 'imposer'라고 첫 글자를 'i'로 썼으면 반드시 'i-m-p-o-s-e-r'로 기록해야 합니다.\n"
            "2. 단, 학생이 정답을 맞게 썼으나 'r', 'i', 'e', 'u' 등의 획이 작거나 흐릿하여 겉보기에 빠진 것처럼 보일 수 있으니 이미지의 해당 글자 획을 극도로 정밀하게 재확인하세요.\n"
            "3. 미응답 또는 공란인 경우 반드시 '(미응답)'으로 작성하세요."
        )
    )


class GradingSchema(BaseModel):
    details: list[QuestionResult]


st.markdown("### 1단계: 학생 시험지 사진 업로드")
student_photos = st.file_uploader(
    "학생 시험지 사진 업로드 (다중 선택 가능)",
    type=["jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True,
    key="photo_uploader",
)

st.markdown("### 2단계: 교재 정답지 엑셀 파일 업로드")
answer_files = st.file_uploader(
    "정답지 엑셀/CSV 파일 선택 (다중 선택 가능)",
    type=["xlsx", "xls", "csv"],
    accept_multiple_files=True,
    key="answer_uploader",
)


def compress_and_encode_image(uploaded_file, max_size=3840):
    file_bytes = uploaded_file.getvalue()
    image = Image.open(io.BytesIO(file_bytes))

    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass

    if image.mode != "RGB":
        image = image.convert("RGB")

    image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=98, subsampling=0)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def clean_spelled_letters(raw_letters: str) -> str:
    """하이픈을 제거하고 알파벳과 공백을 유지한 정규화된 문자열 반환"""
    if not raw_letters:
        return ""
    text = unicodedata.normalize("NFC", str(raw_letters)).lower().strip()
    # 공백 구분을 유지하면서 하이픈 및 특수문자 제거
    text = re.sub(r"[-_.,]", "", text)
    # 연속된 공백 하나로 축소
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_text(text: str) -> str:
    """정답지 영단어/문장 정규화 (소문자화 및 불필요한 문장부호 제거, 공백 유지)"""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text)).lower().strip()
    # 영문자, 숫자, 공백만 남기고 제거
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_english_text(text: str) -> bool:
    return bool(re.search(r"[a-zA-Z]", text))


def load_answer_dict_from_file(ans_file) -> dict:
    answer_bytes = ans_file.getvalue()
    f_name = ans_file.name.lower()

    if f_name.endswith(".csv"):
        df_sub = pd.read_csv(io.BytesIO(answer_bytes), header=None).dropna(
            how="all"
        )
    else:
        df_sub = pd.read_excel(
            io.BytesIO(answer_bytes), header=None, engine="openpyxl"
        ).dropna(how="all")

    raw_q_nums = df_sub.iloc[:, 0].astype(str)
    raw_q_ans = df_sub.iloc[:, 1].astype(str).str.strip()

    ans_dict = {}
    for q_n, q_a in zip(raw_q_nums, raw_q_ans):
        norm_key = normalize_q_num(q_n)
        if norm_key:
            ans_dict[norm_key] = q_a

    return ans_dict


answer_file_map = {}
if answer_files:
    for ans_f in answer_files:
        a_key = extract_key_number(ans_f.name)
        if a_key:
            answer_file_map[a_key] = ans_f

st.markdown("---")

start_grading = st.button("🚀 채점 시작", type="primary", use_container_width=True)

if start_grading:
    if not api_key or not answer_files or not student_photos:
        st.error(
            "API 키, 학생 시험지 사진, 교재 정답지 엑셀 파일을 모두 업로드해 주세요."
        )
    else:
        try:
            client = OpenAI(api_key=api_key)
            st.markdown("### 📊 채점 결과")

            for idx, photo in enumerate(student_photos):
                photo_key = extract_key_number(photo.name)

                st.markdown(
                    "#### 📄 [{}/{}] 파일명: `{}` (번호: `{}`)".format(
                        idx + 1, len(student_photos), photo.name, photo_key
                    )
                )

                matched_answer_file = answer_file_map.get(photo_key)
                if not matched_answer_file:
                    st.error(
                        "❌ `{}`에 매칭되는 정답지(`{}`)를 찾지 못했습니다.".format(
                            photo.name, photo_key
                        )
                    )
                    continue

                answer_dict = load_answer_dict_from_file(matched_answer_file)
                base64_image = compress_and_encode_image(photo, max_size=3840)

                # 정답 리스트를 텍스트 형태로 프롬프트에 추가하여 Vision 모델이 정밀 비교하도록 지원
                answer_context_str = "\n".join(
                    ["- {}번 정답 기준: {}".format(k, v) for k, v in answer_dict.items()]
                )

                prompt = (
                    "당신은 영단어 및 문장 시험지의 학생 손글씨를 철저히 검증하는 초정밀 OCR 판독관입니다.\n\n"
                    "📋 [참고용 해당 시험지 문항별 정답 리스트]:\n"
                    f"{answer_context_str}\n\n"
                    "🚨 [초엄격 손글씨 추출 및 오답 판독 절대 규칙]\n"
                    "1. [정밀 획 판독 - 'r', 'i', 'e', 'u' 등 미세 알파벳 누락 방지]:\n"
                    "   - 학생이 작성한 글자 중 특히 't-r-e-a-s-u-r-e'의 'r'이나 'c-o-n-s-t-r-u-c-t'의 'r'처럼 단어 중간/앞쪽에 작게 써진 알파벳 획이 존재하는지 극도로 주의하여 눈으로 재확인하세요.\n"
                    "   - 알파벳 획이나 곡선이 아주 작게라도 존재한다면 절대 생략하지 말고 포함하여 판독하세요.\n"
                    "2. [지레짐작/자체보정 절대 금지]:\n"
                    "   - 정답 문맥상 원래 단어가 'composer'일지라도, 학생이 명확히 'imposer'라고 적었거나 첫 글자를 'i'로 썼다면 눈에 보이는 그대로 'i-m-p-o-s-e-r'로 판독하세요.\n"
                    "   - 학생이 실제로 알파벳을 틀리게 적었거나 아예 쓰지 않은 경우에만 오답으로 기록하세요.\n"
                    "3. [단어 추출 형태]:\n"
                    "   - 알파벳 하나하나를 하이픈(-)으로 구분하여 작성하세요. (예: t-r-e-a-s-u-r-e)\n"
                    "   - 띄어쓰기가 있는 구나 문장의 경우 단어 사이에는 공백을 남기세요. (예: s-u-p-p-o-r-t - a - i-m-p-o-s-e-r)\n"
                    "4. [미응답 처리]:\n"
                    "   - 손글씨 획이 없거나 공란인 문항은 반드시 '(미응답)'으로 작성하세요."
                )

                response = client.beta.chat.completions.parse(
                    model=selected_model,
                    temperature=0.0,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "image_url": {
                                    "url": "data:image/jpeg;base64,{}".format(
                                        base64_image
                                    ),
                                    "detail": "high",
                                },
                                "type": "image_url",
                            },
                        ],
                    }],
                    response_format=GradingSchema,
                )

                parsed_data = response.choices[0].message.parsed
                gpt_parsed = {}
                if parsed_data and parsed_data.details:
                    for item in parsed_data.details:
                        norm_num = normalize_q_num(item.number)
                        gpt_parsed[norm_num] = str(
                            item.spelled_out_letters
                        ).strip()

                records = []
                for q_num_norm, c_ans in answer_dict.items():
                    if q_num_norm in gpt_parsed:
                        raw_spelled = gpt_parsed[q_num_norm]

                        if raw_spelled in [
                            "(미응답)",
                            "미응답",
                            "(공란)",
                            "공란",
                            "none",
                            "null",
                            "",
                        ]:
                            student_word = "(미응답)"
                            is_correct = False
                            reason = "미응답 (공란)"
                        else:
                            student_word = clean_spelled_letters(raw_spelled)
                            correct_word = normalize_text(str(c_ans))

                            if not is_english_text(student_word):
                                is_correct = False
                                reason = "영단어 미작성"
                            elif student_word == correct_word:
                                is_correct = True
                                reason = "정답"
                            else:
                                is_correct = False
                                reason = (
                                    "철자 불일치/누락 (작성: '{}' / 정답: '{}')"
                                    "".format(student_word, correct_word)
                                )
                    else:
                        student_word = "(미응답)"
                        is_correct = False
                        reason = "시험지에서 해당 문항 번호를 찾지 못함 (미인식/미응답)"

                    records.append({
                        "문항 번호": q_num_norm,
                        "학생 작성 답안": student_word,
                        "교재 정답": str(c_ans).strip(),
                        "정오답": is_correct,
                        "사유": reason,
                    })

                total_q_count = len(records)
                total_correct = sum(1 for r in records if r["정오답"])
                wrong_count = total_q_count - total_correct

                wrong_details = [
                    {
                        "문항 번호": r["문항 번호"],
                        "학생 작성 답안": r["학생 작성 답안"],
                        "교재 정답": r["교재 정답"],
                        "사유": r["사유"],
                    }
                    for r in records
                    if not r["정오답"]
                ]

                st.info(
                    "결과: **{} / {}점** (틀린 문항: {}개)".format(
                        total_correct, total_q_count, wrong_count
                    )
                )

                if wrong_details:
                    df_wrong = pd.DataFrame(wrong_details)
                    st.dataframe(
                        df_wrong, use_container_width=True, hide_index=True
                    )

                    csv_data = df_wrong.to_csv(index=False).encode("utf-8-sig")
                    safe_filename = re.sub(r"[^\w\-_.]", "_", photo.name)
                    st.download_button(
                        label="📥 `{}` 오답노트 다운로드 (CSV)".format(
                            photo.name
                        ),
                        data=csv_data,
                        file_name="오답노트_{}.csv".format(safe_filename),
                        mime="text/csv",
                        key="dl_{}".format(idx),
                    )
                else:
                    st.balloons()
                    st.success("🎉 모든 문항을 맞혔습니다!")

                st.markdown("---")

        except Exception as e:
            st.error("채점 중 오류가 발생했습니다: {}".format(e))
