import streamlit as st
import pandas as pd
from openai import OpenAI
from PIL import Image
import json

st.title("📝 영단어 시험 자동 채점")
st.write("갤럭시 S25로 찍은 답안지와 엑셀 정답지를 올려 채점하세요.")

api_key = st.text_input("OpenAI API Key 입력", type="password")
answer_file = st.file_uploader("교재 정답지 엑셀 파일 업로드 (.xlsx)", type=["xlsx"])
student_photos = st.file_uploader("학생 답안지 사진 업로드", type=["jpg", "jpeg", "png"], accept_multiple_files=True)

if st.button("채점 시작하기"):
    if not api_key or not answer_file or not student_photos:
        st.error("API 키, 정답지, 사진을 모두 입력/업로드해주세요.")
    else:
        client = OpenAI(api_key=api_key)
        df_answers = pd.read_excel(answer_file)
        answer_dict = dict(zip(df_answers.iloc[:, 0].astype(str), df_answers.iloc[:, 1].astype(str).str.strip()))
        
        for photo in student_photos:
            st.write(f"--- 📄 채점 중: {photo.name} ---")
            image = Image.open(photo)
            st.success(f"{photo.name} 처리 완료!")
