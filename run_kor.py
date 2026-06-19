import uvicorn

if __name__ == "__main__":
    # 한국주식 자동매매 서버 (미국주식 app 은 8000, 한국주식 app_kor 는 8001)
    uvicorn.run("app_kor.main:app", host="0.0.0.0", port=8001, reload=False)
