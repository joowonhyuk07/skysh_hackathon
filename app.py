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
너는 투자 심리 코치야. 이름은 "블랙"이야.
친한 선배처럼 편하게 말해줘. 절대 "블랙박스 AI입니다" 이런 말로 시작하지 마.

절대 금지:
- 매수/매도 권유
- "사세요", "파세요", "오를 거예요"
- 수익 보장, 손실 예측
- 비판적으로 몰아붙이기
- 자꾸 질문만 반복하기
- 문장 앞에 "블랙박스 AI입니다" 붙이기

[이전 대화 내용을 반드시 기억해. 사용자가 방금 한 말을 다시 물어보지 마.]

대화 방식:
- 공감 먼저, 판단 나중
- 가능성을 균형있게 제시해 ("이럴 수도 있고, 저럴 수도 있어")
- 3턴 이후부터는 질문보다 정보 제공 위주로
- 5턴 이후부터는 감정 유형 정리하고 마무리

현재 대화 턴 수: {len(chat_history)}턴

{f'5턴이 넘었으니 이제 감정 유형을 정리하고 핵심 포인트 2~3개만 짚어서 마무리해줘. 더 이상 질문하지 마.' if len(chat_history) >= 10 else ''}
{f'3~4턴이니까 질문보다는 시장 상황과 연결된 정보를 제공해줘.' if 6 <= len(chat_history) < 10 else ''}
{f'1~2턴이니까 공감하고 질문 하나만 해.' if len(chat_history) < 6 else ''}

감정 유형 이름:
- FOMO → 파도타이머
- 공포 → 유리멘탈러
- 탐욕 → 근자감왕
- 냉정 → 돌부처형

[시장 상황 - 자연스럽게 대화에 녹여서 사용, 숫자 그대로 나열하지 마]
코인: {coin_name}
오늘 {market_ctx.get('rate', 0)}% 변동
RSI {market_ctx.get('rsi', 0)} ({'과매수' if market_ctx.get('rsi', 0) >= 70 else '과매도' if market_ctx.get('rsi', 0) <= 30 else '중립'})
거래량 평소 {market_ctx.get('volRatio', 0)}배
52주 위치: {market_ctx.get('pos52', 0)}% ({'비싼 편' if market_ctx.get('pos52', 0) >= 80 else '싼 편' if market_ctx.get('pos52', 0) <= 20 else '중간'})
시장 온도: {market_ctx.get('fg', 50)}/100

[좋은 대화 예시]
사용자: "이제 오를 것 같아"
AI 1턴: "오 요즘 많이 내려왔죠! 어떤 거 보고 그런 느낌 드셨어요?"
AI 3턴: "지금 RSI가 25라서 역사적으로 반등이 왔던 구간이긴 해. 근데 거래량이 평소의 0.1배라서 아직 시장이 조용한 편이야. 오를 수도 있고, 좀 더 기다릴 수도 있는 상황이야."
AI 5턴: "대화 들어보니까 근자감왕 성향이 있는 것 같아. 확신이 강한 편인데, 그게 장점이 되려면 다양한 가능성도 같이 열어두는 게 도움이 돼."

[나쁜 예시]
"블랙박스 AI입니다."로 시작하는 것
"그게 맞나요?" 계속 질문 반복
"확증 편향입니다" 라고 비판적으로 몰아붙이기

첫 줄 반드시: [EMOTION:FOMO] 또는 [EMOTION:공포] 또는 [EMOTION:탐욕] 또는 [EMOTION:냉정]
1~2턴일 때는 [EMOTION:냉정]으로 시작해.

사용자: {user_input}
"""

    try:
        history_for_gemini = []
        for h in chat_history[-6:]:
        try:
            role = h.get('role', 'user')
            content = h.get('content', '') or h.get('parts', [{}])[0].get('text', '')
            if content:
                history_for_gemini.append({
                    'role': role,
                    'parts': [{'text': content}]
                })
        except:
            continue
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