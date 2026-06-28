from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import google.generativeai as genai
import requests
import feedparser
from datetime import datetime, timezone
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///blackbox.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.5-flash')

# DB 모델
class Analysis(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    coin = db.Column(db.String(50))
    input_text = db.Column(db.Text)
    emotion = db.Column(db.String(20))
    result = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

# Upbit API 프록시 (CORS 해결)
@app.route('/api/upbit/<path:path>')
def upbit_proxy(path):
    url = f'https://api.upbit.com/v1/{path}'
    params = request.args.to_dict()
    try:
        res = requests.get(url, params=params, headers={'accept': 'application/json'})
        return jsonify(res.json())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# Gemini AI 분석
@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.json
    user_input = data.get('input', '')
    coin_name = data.get('coin', '')
    market_ctx = data.get('context', {})
    chat_history = data.get('history', [])

    prompt = f"""
너는 10년 경력의 투자 심리 상담사야. 이름은 "블랙박스 AI"야.
따뜻하지만 솔직하게, 친한 선배처럼 말해줘.
말투는 친근하게, 전문 용어는 반드시 괄호로 쉽게 설명해줘.

절대 금지:
- 매수/매도 권유
- "사세요", "파세요", "오를 거예요", "떨어질 거예요"
- 수익 보장, 손실 예측
- 여러 개 질문 한 번에 하기

대화 단계 (현재 대화 턴 수: {len(chat_history)}턴):

1턴이면: 공감 먼저, 질문 딱 하나만 해. 분석 결과 절대 먼저 말하지 마.
2턴이면: 시장 데이터를 초보자 언어로 자연스럽게 연결해서 설명하고 질문 하나.
3턴이면: 감정 유형 진단해줘. [EMOTION:태그] 넣고 유형 이름으로 불러줘.
4턴 이상이면: 관련 투자 심리 지식 하나 알려주고 열린 질문으로 마무리.

감정 유형 이름:
- FOMO → "파도타이머"
- 공포 → "유리멘탈러"  
- 탐욕 → "근자감왕"
- 냉정 → "돌부처형"

[현재 시장 상황 - 대화에 자연스럽게 녹여서 사용]
코인: {coin_name}
오늘 {market_ctx.get('rate', 0)}% 변동
RSI {market_ctx.get('rsi', 0)} ({'과매수 구간이라 단기 과열 상태야' if market_ctx.get('rsi', 0) >= 70 else '과매도 구간이라 많이 내려온 상태야' if market_ctx.get('rsi', 0) <= 30 else '중립 구간이야'})
거래량 평소 {market_ctx.get('volRatio', 0)}배
매수/매도 압력: {market_ctx.get('pressure', '')}
52주 위치: {market_ctx.get('pos52', 0)}% ({'1년 중 꽤 비싼 편이야' if market_ctx.get('pos52', 0) >= 80 else '1년 중 꽤 싼 편이야' if market_ctx.get('pos52', 0) <= 20 else '중간 가격대야'})
시장 온도: {market_ctx.get('fg', 50)}/100

[좋은 대화 예시]
사용자: "비트코인 오른다고 해서 사려고요"
AI 1턴: "요즘 비트코인 얘기 많이 들리죠! 어디서 오른다는 얘기 들으셨어요?"
AI 2턴: "유튜브 보고 관심 생기셨군요. 근데 지금 비트코인이 1년 중 꽤 저렴한 구간에 있어요. 이런 상황인지 알고 계셨어요?"
AI 3턴: "[EMOTION:FOMO] 말씀 들어보니 파도타이머 성향이 느껴져요. 남들 수익 얘기 듣고 나만 빠지는 것 같은 느낌이요. 맞나요?"
AI 4턴: "파도타이머들이 가장 많이 하는 실수가 뭔지 알아요? 2021년 버블 때 유튜브 보고 고점에 산 사람이 제일 많았거든요. 지금 이 코인, 안 사면 정말 기회를 놓치는 건가요?"

[나쁜 예시 - 절대 하지 마]
"RSI가 25입니다. 과매도 구간으로 반등 가능성이 있습니다."
"지금 매수하기 좋은 타이밍입니다."

첫 줄 반드시: [EMOTION:FOMO] 또는 [EMOTION:공포] 또는 [EMOTION:탐욕] 또는 [EMOTION:냉정]
1턴일 때는 [EMOTION:냉정] 으로 시작하고 분석은 나중에 해.

사용자: {user_input}
"""

    try:
        history_for_gemini = []
        for h in chat_history[-6:]:
            history_for_gemini.append({
                'role': h['role'],
                'parts': [{'text': h['content']}]
            })
        history_for_gemini.append({'role': 'user', 'parts': [{'text': prompt}]})

        response = model.generate_content(history_for_gemini)
        result_text = response.text

        import re
        match = re.search(r'\[EMOTION:(FOMO|공포|탐욕|냉정)\]', result_text)
        emotion = match.group(1) if match else '냉정'

        # DB 저장
        analysis = Analysis(
            coin=coin_name,
            input_text=user_input,
            emotion=emotion,
            result=result_text
        )
        db.session.add(analysis)
        db.session.commit()

        return jsonify({
            'result': result_text,
            'emotion': emotion
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 뉴스 분석
@app.route('/api/news', methods=['POST'])
def analyze_news():
    data = request.json
    coin_name = data.get('coin', '')
    market_ctx = data.get('context', {})

    # 코인별 검색 키워드
    coin_keywords = {
        '비트코인 (BTC)': 'bitcoin BTC',
        '이더리움 (ETH)': 'ethereum ETH',
        '리플 (XRP)': 'ripple XRP',
        '솔라나 (SOL)': 'solana SOL',
        '도지코인 (DOGE)': 'dogecoin DOGE',
        '에이다 (ADA)': 'cardano ADA',
        '아발란체 (AVAX)': 'avalanche AVAX',
        '체인링크 (LINK)': 'chainlink LINK'
    }

    keyword = coin_keywords.get(coin_name, coin_name)

    # Google News RSS로 최신 뉴스 가져오기
    try:
        # 여러 RSS 소스 시도
        rss_urls = [
            f'https://news.google.com/rss/search?q={keyword}+crypto&hl=ko&gl=KR&ceid=KR:ko',
            f'https://feeds.feedburner.com/coindesk/rss/articlefeeds',
            f'https://cointelegraph.com/rss'
        ]
        articles = []
        for rss_url in rss_urls:
            try:
                headers = {'User-Agent': 'Mozilla/5.0'}
                response_rss = requests.get(rss_url, headers=headers, timeout=5)
                feed = feedparser.parse(response_rss.content)
                for entry in feed.entries[:5]:
                    title = entry.get('title', '')
                    if keyword.split()[0].lower() in title.lower() or True:
                        articles.append({
                            'title': title,
                            'link': entry.get('link', ''),
                            'published': entry.get('published', ''),
                            'summary': entry.get('summary', '')[:200] if entry.get('summary') else ''
                        })
                if articles:
                    break
            except:
                continue
        articles = articles[:5]
        print(f"articles 개수: {len(articles)}")
    except Exception as e:
        print(f"뉴스 에러: {e}")
        articles = []

    # 뉴스 없으면 Gemini 자체 지식 사용
    if articles:
        news_text = '\n'.join([f"- {a['title']}" for a in articles])
        prompt = f"""
다음은 {coin_name} 관련 최신 뉴스 기사 제목들이야:
{news_text}

현재 시장 상황:
- 현재가: {market_ctx.get('price', 0):,}원
- 오늘 등락률: {market_ctx.get('rate', 0)}%
- RSI: {market_ctx.get('rsi', 0)}
- 시장 온도: {market_ctx.get('fg', 50)}/100

위 뉴스들을 초보 투자자가 이해할 수 있게 분석해줘.

형식:
## 📰 주요 뉴스 요약
(각 뉴스를 한 줄씩 쉽게 설명)

## 🔍 시장에 미치는 영향
(이 뉴스들이 가격에 어떤 영향을 줄 수 있는지 2~3문장, 가능성으로만 설명)

## 💡 초보자가 알아두면 좋은 것
(이 상황에서 초보자가 알아야 할 개념 1가지)

마지막에 반드시: "※ 이 분석은 참고용이며 투자 권유가 아닙니다."

절대 금지: 매수/매도 권유, 수익 보장 표현
"""
    else:
        prompt = f"""
{coin_name}의 최근 시장 동향과 주요 이슈를 설명해줘.
현재가: {market_ctx.get('price', 0):,}원, 등락률: {market_ctx.get('rate', 0)}%

형식:
## 📰 최근 동향
## 🔍 주요 이슈
## 💡 초보자 포인트

마지막에: "※ 이 분석은 참고용이며 투자 권유가 아닙니다."
절대 금지: 매수/매도 권유
"""

    try:
        print(f"articles 개수: {len(articles)}")
        response = model.generate_content(prompt)
        return jsonify({
            'result': response.text,
            'articles': articles
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 분석 기록 조회
@app.route('/api/history')
def get_history():
    records = Analysis.query.order_by(Analysis.created_at.desc()).limit(50).all()
    return jsonify([{
        'id': r.id,
        'coin': r.coin,
        'input': r.input_text,
        'emotion': r.emotion,
        'time': r.created_at.strftime('%m/%d %H:%M')
    } for r in records])

# 감정 통계
@app.route('/api/stats')
def get_stats():
    records = Analysis.query.all()
    counts = {}
    for r in records:
        counts[r.emotion] = counts.get(r.emotion, 0) + 1
    return jsonify(counts)


@app.route('/')
def index():
    with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'text/html'}

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)