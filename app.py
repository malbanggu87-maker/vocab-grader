import base64
import io
import json
import re
import unicodedata
from PIL import Image, ImageOps
from openai import OpenAI
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Grit English 스마트 채점실", page_icon="📝", layout="wide"
)

st.title("📝 Grit English 스마트 채점실 (가로 전체 행 배너 검수 모드)")
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
)


def extract_key_number(filename: str) -> str:
    name_without_ext = re.sub(r"\.[^.]+$", "", filename)
    numbers = re.findall(r"\d+", name_without_ext)
    if numbers:
        last_num = numbers[-1]
        return last_num.zfill(2)[-2:]
    return ""


def normalize_q_num(q_str: str) -> str:
    if not q_str:
        return ""
    q_clean = str(q_str).strip()
    q_clean = re.sub(r"\.0$", "", q_clean)
    q_clean = re.sub(r"[^\d]", "", q_clean)
    if q_clean.isdigit():
        return str(int(q_clean))
    return str(q_str).strip()


def prepare_image(uploaded_file):
    file_bytes = uploaded_file.getvalue()
    image = Image.open(io.BytesIO(file_bytes))
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass
    if image.mode != "RGB":
        image = image.convert("RGB")
    return image


def crop_wide_row_banner(image: Image.Image, q_num: int, total_q_count: int = 36):
    """
    [새로운 검수 스타일] 잘림 현상을 완벽히 해결하기 위해
    표의 왼쪽(문항번호/뜻)부터 오른쪽(손글씨 답란)까지 가로 전체 행을 넓게 캡처하는 배너 함수
    """
    width, height = image.size
    if total_q_count <= 25:
        top_margin = height * 0.155
        mid_table_y = height * 0.525
        if 1 <= q_num <= 10:
            row_h = (mid_table_y - top_margin) / 10.0
            idx = q_num - 1
            # 💡 좌측 칼럼 전체 가로 폭 (번호 + 뜻 + 답란 모두 포함)
            x1, x2 = int(width * 0.05), int(width * 0.51)
            y1 = int(top_margin + (idx * row_h))
            y2 = int(top_margin + ((idx + 1) * row_h))
        elif 11 <= q_num <= 20:
            row_h = (mid_table_y - top_margin) / 10.0
            idx = q_num - 11
            # 💡 우측 칼럼 전체 가로 폭
            x1, x2 = int(width * 0.51), int(width * 0.98)
            y1 = int(top_margin + (idx * row_h))
            y2 = int(top_margin + ((idx + 1) * row_h))
        else:
            bot_top = height * 0.54
            bot_bot = height * 0.95
            sub_h = (bot_bot - bot_top) / 5.0
            idx = q_num - 21
            # 💡 하단 서술형/문장형 영역 전체 폭
            x1, x2 = int(width * 0.05), int(width * 0.98)
            y1 = int(bot_top + (idx * sub_h))
            y2 = int(bot_top + ((idx + 1) * sub_h))
    else:
        top_margin = height * 0.17
        bottom_margin = height * 0.96
        content_height = bottom_margin - top_margin
        row_h = content_height / 18.0
        if 1 <= q_num <= 18:
            idx = q_num - 1
            x1, x2 = int(width * 0.05), int(width * 0.51)
            y1 = int(top_margin + (idx * row_h))
            y2 = int(top_margin + ((idx + 1) * row_h))
        else:
            idx = q_num - 19
            x1, x2 = int(width * 0.51), int(width * 0.99)
            y1 = int(top_margin + (idx * row_h))
            y2 = int(top_margin + ((idx + 1) * row_h))

    # 상하 여유를 충분히 주어 글자가 절대 잘리지 않도록 함
    padding_y = int((y2 - y1) * 0.3)
    
    crop_box = (
        max(0, x1),
        max(0, y1 - padding_y),
        min(width, x2),
        min(height, y2 + padding_y)
    )
    return image.crop(crop_box)


def image_to_base64(pil_img: Image.Image) -> str:
    buffer = io.BytesIO()
    pil_img.save(buffer, format="JPEG", quality=95)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text)).lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_english_text(text: str) -> bool:
    return bool(re.search(r"[a-zA-Z]", text))


def load_answer_dict_from_file(ans_file) -> dict:
    answer_bytes = ans_file.getvalue()
    f_name = ans_file.name.lower()

    if f_name.endswith(".csv"):
        df_sub = pd.read_csv(io.BytesIO(answer_bytes), header=None).dropna(how="all")
    else:
        df_sub = pd.read_excel(io.BytesIO(answer_bytes), header=None, engine="openpyxl").dropna(how="all")

    raw_q_nums = df_sub.iloc[:, 0].astype(str)
    raw_q_ans = df_sub.iloc[:, 1].astype(str).str.strip()

    ans_dict = {}
    for q_n, q_a in zip(raw_q_nums, raw_q_ans):
        norm_key = normalize_q_num(q_n)
        if norm_key:
            ans_dict[norm_key] = q_a

    return ans_dict


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

answer_file_map = {}
if answer_files:
    for ans_f in answer_files:
        a_key = extract_key_number(ans_f.name)
        if a_key:
            answer_file_map[a_key] = ans_f

st.markdown("---")

start_grading = st.button("🚀 문항별 독립 정밀 채점 시작", type="primary", use_container_width=True)

if start_grading:
    if not api_key or not answer_files or not student_photos:
        st.error("API 키, 학생 시험지 사진, 교재 정답지 엑셀 파일을 모두 업로드해 주세요.")
    else:
        try:
            client = OpenAI(api_key=api_key)
        except Exception as e:
            st.error("OpenAI 클라이언트 초기화 오류: {}".format(e))
            client = None

        if client:
            for idx, photo in enumerate(student_photos):
                photo_key = extract_key_number(photo.name)
                matched_answer_file = answer_file_map.get(photo_key)
                
                if not matched_answer_file:
                    st.error("❌ 파일명 `{}`에 해당하는 번호(`{}`)의 정답지 파일을 찾지 못했습니다!".format(photo.name, photo_key))
                    continue

                answer_dict = load_answer_dict_from_file(matched_answer_file)
                if not answer_dict:
                    st.error("❌ 정답지 파일 `{}`의 내용을 읽어오지 못했습니다.".format(matched_answer_file.name))
                    continue

                full_image = prepare_image(photo)
                total_q_count = len(answer_dict)

                records = []
                system_role = (
                    "당신은 영단어 시험지 전용 정밀 문자 판독(OCR) 및 채점 시스템입니다.\n"
                    "제공된 단 하나의 문항 영역 이미지와 교재 정답을 비교하여 판정하십시오.\n"
                    "반드시 아래 JSON 형식으로만 응답하십시오:\n"
                    "{\n"
                    '  "written": "학생이 작성한 단어 그대로 (공란이면 미응답)",\n'
                    '  "status": "정확한정답 또는 자체보정 또는 오답"\n'
                    "}"
                )

                progress_bar = st.progress(0, text=f"[{photo.name}] 채점 진행 중...")
                total_items = len(answer_dict)

                for item_idx, (q_num_norm, c_ans) in enumerate(answer_dict.items()):
                    if not q_num_norm.isdigit():
                        continue
                    
                    q_int = int(q_num_norm)
                    # 💡 가로 전체 배너 크롭 적용
                    cropped_img = crop_wide_row_banner(full_image, q_int, total_q_count)
                    img_b64 = image_to_base64(cropped_img)

                    prompt = (
                        f"이 이미지는 시험지의 {q_num_norm}번 문항 영역입니다.\n"
                        f"교재 정답은 '{c_ans}'입니다.\n"
                        "학생이 쓴 손글씨를 읽고 정답과 비교하여 결과를 JSON으로 출력하세요."
                    )

                    try:
                        response = client.chat.completions.create(
                            model=selected_model,
                            temperature=0.0,
                            response_format={"type": "json_object"},
                            messages=[
                                {"role": "system", "content": system_role},
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": prompt},
                                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}", "detail": "high"}}
                                    ]
                                }
                            ]
                        )

                        res_data = json.loads(response.choices[0].message.content)
                        raw_written = res_data.get("written", "(미응답)").strip()
                        status = res_data.get("status", "오답").strip()

                        if raw_written in ["(미응답)", "미응답", "공란", "none", "null", ""]:
                            is_correct = False
                            reason = "미응답 (공란)"
                            is_flagged = True
                        else:
                            student_word = normalize_text(raw_written)
                            correct_word = normalize_text(str(c_ans))
                            
                            if student_word == correct_word:
                                is_correct = True
                                reason = "정확한 정답"
                                is_flagged = False
                            elif "자체보정" in status:
                                is_correct = True
                                reason = "AI 자체 보정 정답 (확인 필요)"
                                is_flagged = True
                            else:
                                is_correct = False
                                reason = f"철자 오류/오기 (작성: '{raw_written}' / 정답: '{c_ans}')"
                                is_flagged = True

                    except Exception as item_err:
                        raw_written = "(판독 오류)"
                        is_correct = False
                        reason = f"API 오류: {item_err}"
                        is_flagged = True

                    records.append({
                        "문항 번호": q_num_norm,
                        "학생 작성 답안": raw_written,
                        "교재 정답": str(c_ans).strip(),
                        "정오답": is_correct,
                        "사유": reason,
                        "검수필요": is_flagged
                    })

                    progress_bar.progress((item_idx + 1) / total_items, text=f"[{photo.name}] {item_idx + 1}/{total_items} 문항 채점 완료")

                progress_bar.empty()
                st.session_state["records_{}".format(idx)] = records
                st.session_state["photo_{}".format(idx)] = full_image
                st.session_state["total_q_{}".format(idx)] = total_q_count

for idx, photo in enumerate(student_photos if student_photos else []):
    rec_key = "records_{}".format(idx)
    if rec_key in st.session_state:
        records = st.session_state[rec_key]
        full_img = st.session_state["photo_{}".format(idx)]
        total_q = st.session_state["total_q_{}".format(idx)]

        st.markdown("---")
        st.markdown(f"### 📄 파일명: `{photo.name}` 정밀 채점 완료")

        correct_count = sum(1 for r in records if r["정오답"])
        review_items = [r for r in records if r["검수필요"]]

        st.info(f"📊 최종 점수: **{correct_count} / {len(records)}점** | 🤖 AI 완벽 자동 통과: **{len(records) - len(review_items)}개** | ⚠️ 확인이 필요한 검수 문항: **{len(review_items)}개**")

        if review_items:
            st.markdown("#### 🔍 [배너형 스마트 검수] 아래의 넓은 가로 배너를 통해 잘림 없이 눈으로 확인하세요")
            
            for item in review_items:
                q_num = item["문항 번호"]
                is_currently_correct = item["정오답"]

                # 💡 레이아웃 개선: 상단에 정보를 보여주고, 그 아래에 큼직한 가로 배너 이미지를 통째로 배치
                with st.container(border=True):
                    col_info, col_btn = st.columns([3, 1])
                    
                    with col_info:
                        st.markdown(f"**📌 {q_num}번 문항** | 교재 정답: **{item['교재 정답']}** | AI 판독: `{item['학생 작성 답안']}`")
                        if is_currently_correct:
                            st.warning(f"상태: 정답 ({item['사유']})")
                        else:
                            st.error(f"상태: 오답 ({item['사유']})")

                    with col_btn:
                        st.write("")
                        if is_currently_correct:
                            if st.button("❌ 틀렸음 (오답)", key=f"to_wrong_{idx}_{q_num}", use_container_width=True):
                                for r in records:
                                    if r["문항 번호"] == q_num:
                                        r["정오답"] = False
                                        r["검수필요"] = False
                                        r["사유"] = "선생님 검수: 오답 정정"
                                st.session_state[rec_key] = records
                                st.rerun()
                        else:
                            if st.button("⭕ 정답 인정", key=f"to_corr_{idx}_{q_num}", use_container_width=True):
                                for r in records:
                                    if r["문항 번호"] == q_num:
                                        r["정오답"] = True
                                        r["검수필요"] = False
                                        r["사유"] = "선생님 검수: 정답 인정"
                                st.session_state[rec_key] = records
                                st.rerun()

                    # 💡 가로 전체 배너 이미지 출력 (너비를 넓혀서 잘림 방지)
                    if q_num.isdigit():
                        banner_img = crop_wide_row_banner(full_img, int(q_num), total_q)
                        st.image(banner_img, caption=f"[{q_num}번 문항 가로 전체 행 배너 View]", use_container_width=True)

        else:
            st.success("🎉 AI가 모든 문항을 완벽하게 처리하여 검수할 항목이 없습니다!")

        st.markdown("---")

        df_wrong = pd.DataFrame([r for r in records if not r["정오답"]])
        if not df_wrong.empty:
            csv_data = df_wrong[["문항 번호", "학생 작성 답안", "교재 정답", "사유"]].to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="📥 최종 확정 오답노트 다운로드 (CSV)",
                data=csv_data,
                file_name="오답노트_{}.csv".format(photo.name),
                key="dl_final_{}".format(idx)
            )
        else:
            st.balloons()
            st.success("🎉 최종 확정된 오답이 없습니다!")
