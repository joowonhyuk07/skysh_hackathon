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


@app.route('/api/coin/<market>')
def get_coin_data(market):
    try:
        import concurrent.futures
        def get_ticker():
            return requests.get(f'https://api.upbit.com/v1/ticker?markets={market}', headers={'accept':'application/json'}).json()
        def get_candles():
            return requests.get(f'https://api.upbit.com/v1/candles/days?market={market}&count=14', headers={'accept':'application/json'}).json()
        def get_orderbook():
            return requests.get(f'https://api.upbit.com/v1/orderbook?markets={market}', headers={'accept':'application/json'}).json()

        with concurrent.futures.ThreadPoolExecutor() as executor:
            f1 = executor.submit(get_ticker)
            f2 = executor.submit(get_candles)
            f3 = executor.submit(get_orderbook)
            ticker = f1.result()
            candles = f2.result()
            orderbook = f3.result()

        return jsonify({ 'ticker': ticker, 'candles': candles, 'orderbook': orderbook })
    except Exception as e:
        return jsonify({'error': str(e)}), 500



# 감정 통계
@app.route('/api/stats')
def get_stats():
    records = Analysis.query.all()
    counts = {}
    for r in records:
        counts[r.emotion] = counts.get(r.emotion, 0) + 1
    return jsonify(counts)

@app.route('/data/<filename>')
def serve_data(filename):
    return app.send_static_file(filename)

@app.route('/api/timemachine/<year>')
def get_timemachine_data(year):
    import json
    news_db = {
        "2020": {
            "2020-06-22 09:00": ("페이팔, 3억 명 회원 기반으로 암호화폐 결제 준비 중", "글로벌 결제 공룡 페이팔이 암호화폐 거래 서비스를 개시하며 전통 자금 유입의 신호탄을 쏘았습니다."),
            "2020-07-22 18:00": ("미국 통화감독청(OCC) 연방은행에 가상자산 수탁 서비스 전격 허용", "미국 재무부 산하 통화감독청이 미국의 모든 국립은행과 연방 저축은행들이 고객들을 위해 비트코인 등 가상자산 수탁 서비스를 제공할 수 있다고 공식 발표했습니다."),
            "2020-08-11 09:00": ("[공시] 나스닥 상장사 마이크로스트레티지, 현금 대신 비트코인 대규모 매입", "미국 비즈니스 인텔리전스 기업이 인플레이션 헤지를 위해 회사 예비 자산으로 비트코인을 사들이며 기업 비축 트렌드를 촉발했습니다."),
            "2020-09-04 10:00": ("[시황] 미국 증시 급락에 비트코인도 1만 달러 붕괴… 동조화 심화", "기술주 중심의 나스닥 지수가 폭락하자 비트코인 역시 동반 급락하며 하루 만에 10% 이상 하락, 단기 패닉셀을 유발하고 있습니다."),
            "2020-10-21 09:00": ("[속보] 페이팔, 가상자산 결제 및 매매 공식 출시", "6월 소문으로만 돌던 페이팔의 서비스가 공식화되며 연고점을 경신, 한 달간 약 30%에 가까운 폭발적인 랠리를 시작했습니다."),
            "2020-11-17 10:00": ("[시황] 비트코인 1만 7000달러 돌파, '디지털 금' 명성 굳히나", "코로나19 사태 이후 막대한 시중 유동성이 풀린 가운데 비트코인이 연초 대비 130% 이상 폭등하며 전통 안전자산인 금과 달러를 대체할 자산으로 빠르게 부상하고 있습니다."),
            "2020-11-24 14:00": ("[시황] 비트코인, 3년 만에 1만 9000달러 돌파… 전고점 턱밑 추격", "기관 투자자들의 지속적인 매수세와 소외되는 것에 대한 두려움(FOMO) 릴레이가 이어지며 역사적 최고가에 바짝 다가섰습니다."),
            "2020-11-27 15:30": ("[속보] 과열된 코인 시장, 전고점 직전 대규모 청산 발생하며 숨고르기", "1만 9000달러선에서 사상 최고가 경신을 앞두고 선물 시장의 과도한 레버리지 포지션이 연쇄 청산되었습니다."),
            "2020-12-16 09:00": ("[파국] 역사가 바뀐 날… 비트코인, 사상 최초 2만 달러 벽 전격 돌파", "심리적 마지노선이자 가장 강력한 저항선이었던 2만 달러가 깨부서지며 매도 매물이 증발, 수직 상승 랠리를 기록 중입니다."),
            "2020-12-25 09:00": ("[특집] 크리스마스… 비트코인 2만 4000달러 돌파하며 사상 최고가 행진", "대형 자산운용사들의 잇따른 매입 인증과 제도권 편입 호재에 힘입어 크리스마스 당일 2만 4000달러를 돌파했습니다.")
        },
        "2022": {
            "2022-05-09 11:00": ("[파국] 한국산 코인 '테라·루나' 1달러 페깅 실패… '죽음의 소용돌이' 시작", "알고리즘 기반 스테이블 코인 테라(UST)의 1달러 고정선이 무너지며 자매 코인인 루나(LUNA)와 함께 투매 물량이 쏟아지고 있습니다."),
            "2022-06-13 09:00": ("[긴급] 가상자산 대출 플랫폼 '셀시우스', 유동성 위기로 전격 출금 중단", "루나 사태의 여파로 가상자산 뱅크런이 확산되는 가운데, 대형 예치·대출 플랫폼 셀시우스가 모든 인출과 이체를 동결했습니다."),
            "2022-07-06 14:00": ("[공시] 가상자산 헤지펀드 '쓰리아로우즈캐피탈(3AC)', 파산보호 신청", "한때 100억 달러의 자산을 굴리던 헤지펀드 3AC가 루나 사태로 인한 마진콜을 견디지 못하고 최종 파산 절차에 돌입했습니다."),
            "2022-08-26 10:30": ("[시황] 미 연준 파월 '잭슨홀 미팅' 매파 발언… 비트코인 급락", "제롬 파월 미 연방준비제도 의장이 강력한 금리 인상 기조를 유지하겠다고 시사하자 비트코인 등 가상자산 시장이 즉각 급락세로 돌아섰습니다."),
            "2022-09-15 15:45": ("[특집] 이더리움 '머지(Merge)' 업데이트 최종 성공", "블록체인 역사상 최대 규모의 업그레이드인 이더리움 머지가 성공적으로 완료되었습니다. 전력 소비량이 99% 이상 감소해 친환경 자산으로 거듭났습니다."),
            "2022-10-12 11:00": ("[규제] 유로의회, 세계 최초 가상자산 포괄적 규제안 'MiCA' 전격 통과", "유럽연합(EU)이 가상자산 시장을 제도권 안에서 관리하기 위해 입법한 미카(MiCA) 법안이 승인되었습니다."),
            "2022-11-04 09:00": ("[외신] 코인데스크 '알라메다 리서치' 대차대조표 부실 의혹 고발", "FTX 거래소의 자매회사 알라메다 리서치의 부실 회계 상태를 폭로했습니다."),
            "2022-11-12 09:00": ("[파국] FTX 거래소 최종 인수 결렬 및 미국 챕터11 파산 보호 신청", "세계 3위 거래소였던 FTX가 유동성 위기를 극복하지 못하고 파산 절차를 밟게 되었습니다."),
            "2022-12-24 16:00": ("[종합] 역사상 최악의 '크립토 윈터' 마감 중", "2022년 가상자산 시장은 대형 기관들의 몰락으로 비트코인이 고점 대비 70% 이상 폭락한 해로 기록되었습니다.")
        },
        "2026": {
            "2025-08-24 10:00": ("[속보] 이더리움(ETH) 사상 최고가 4,950달러 돌파… 알트코인 대규모 랠리", "비트코인의 견고한 상승세에 이어 이더리움이 디파이(DeFi) 및 레이어2 생태계 확장 호재로 사상 최고가를 경신했습니다."),
            "2025-09-18 14:00": ("[시황] 솔라나(SOL) 250달러 돌파, 고성능 블록체인 수요 폭발", "처리 속도와 확장성을 무기로 한 솔라나가 전고점을 돌파하며 시가총액 최상위권 굳히기에 들어갔습니다."),
            "2025-10-06 09:00": ("[파국] 비트코인 12만 6,000달러 돌파하며 역사상 최고점 경신… '광기의 정점'", "비트코인이 마침내 사상 최고가인 12만 6,000달러를 터치하며 전 세계 금융 시장의 주목을 한 몸에 받았습니다."),
            "2025-11-15 11:30": ("[시황] 고점 인식 확산… 비트코인 일주일 만에 -15% 급락하며 숨고르기", "10월 역사적 고점을 찍은 이후 대형 고래들과 채굴자들의 차익실현 물량이 대거 쏟아졌습니다."),
            "2025-12-02 15:00": ("[외신] 에릭 트럼프의 가상자산 기업 40% 폭락… 규제 리스크 수면 위로", "미국 내 정치적 리스크 및 일부 가상자산 프로젝트들의 부실 의혹이 제기되면서 시장 전반에 찬바람이 불고 있습니다."),
            "2026-01-24 09:00": ("[종합] 새해에도 이어지는 하락세… 비트코인 9만 달러선 위태", "새해 반등을 기대했던 투자자들의 바람과 달리, 기관들의 위험자산 회피 성향이 강해지며 비트코인이 9만 달러선 아래로 흘러내리고 있습니다."),
            "2026-02-05 17:30": ("[긴급] 비트코인 6만 3,000달러 붕괴, 고점 대비 '반토막'… 패닉셀 발생", "지속적인 규제 압박과 거시경제 악화로 비트코인이 지난해 10월 고점 대비 50%나 폭락한 6만 3,000달러까지 추락했습니다."),
            "2026-02-28 10:00": ("[시황] 중동 발 지정학적 위기 고조 및 AI 스케어로 기술주·가상자산 동반 폭락", "글로벌 관세 정책 변화와 중동의 지정학적 긴장 고조, 빅테크 기업들의 AI 성장성 재평가 우려가 겹치며 나스닥과 비트코인이 동반 급락했습니다.")
        }
    }
    try:
        with open(f'data_{year}.js', 'r', encoding='utf-8') as f:
            content = f.read()
        json_str = content.replace('const market_data = ', '').replace(';\nexport default market_data;', '').strip()
        data = json.loads(json_str)
        if year in news_db:
            for item in data:
                date_str = item.get('date', '')
                if date_str in news_db[year]:
                    item['hasNews'] = True
                    item['newsTitle'] = news_db[year][date_str][0]
                    item['newsContent'] = news_db[year][date_str][1]
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
@app.route('/api/timemachine/analyze', methods=['POST'])
def analyze_timemachine():
    data = request.json
    trade_logs = data.get('trade_logs', [])
    roi = data.get('roi', 0)
    start_seed = data.get('start_seed', 0)
    final_asset = data.get('final_asset', 0)
    scenario = data.get('scenario', '')
    executed_points = data.get('executed_points', [])

    if not trade_logs:
        return jsonify({'result': '체결된 거래가 없어서 분석할 수 없어요.\n다음엔 매수/매도를 시도해보세요!'})

    trade_summary = '\n'.join(trade_logs)
    points_summary = '\n'.join([
        f"- {p['type']} {p['coin']} {p['price']:,}원 ({p['index']}번째 시점)"
        for p in executed_points
    ])

    prompt = f"""
너는 10년 경력의 투자 행동 분석가야.
아래 모의투자 결과를 보고 매매 행동을 분석해줘.

절대 금지:
- 파도타이머, 유리멘탈러, 근자감왕, 돌부처형 같은 감정 유형 이름 사용 금지
- "이렇게 했어야 한다" 단정 금지
- 매수/매도 권유 금지
- 수익 보장 표현 금지

[시나리오]
{scenario}

[결과]
- 시작 자산: {start_seed:,}원
- 최종 자산: {final_asset:,}원
- 수익률: {roi}%

[체결 로그]
{trade_summary}

[매매 타점]
{points_summary}

형식:

## 🎯 매매 행동 분석
(실제 체결 로그 기반으로 구체적으로. 언제 어떤 행동을 했는지 사실 위주로 2~3문장)

## 🔍 이 매매를 보는 두 가지 시각
시각 1️⃣: (긍정적 해석 - 이 매매의 합리적인 이유)
시각 2️⃣: (다른 해석 - 놓쳤을 수 있는 부분)

## 💡 알아두면 좋은 것
(이 매매 상황과 관련된 투자 지식 1가지. 초보자 언어로)

## 🤔 스스로 생각해볼 것
(판단을 유도하는 열린 질문 1개로 마무리)

마지막에 반드시: "※ 이 분석은 참고용이며 투자 권유가 아닙니다."
"""

    try:
        response = model.generate_content(prompt)
        return jsonify({'result': response.text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/')
def index():
    with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'text/html'}

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)