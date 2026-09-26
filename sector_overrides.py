"""User-curated, single-sector stock classifications.

Names listed in two requested sectors are intentionally omitted until the
user chooses one.  Keep aliases explicit; never use a fuzzy name match here.
"""

SECTOR_STOCKS = {
    "보안·양자": "엑스게이트, 샌즈랩, 드림시큐리티, 드림시큐리",
    "데이터센터": (
        "지엔씨에너지, SGC에너지, 두산퓨얼셀, NHN, LG씨엔에스, LG CNS, "
        "비나텍, 케이아이엔엑스, 가비아, 한미글로벌, 지투파워, 윈스, 윈스테크넷, "
        "SKAI, 알서포트, 데이타솔루션, 파이오링크, SGA솔루션즈, "
        "모니터랩, 한싹, 멤레이비티, 모아데이타"
    ),
    "반도체 설계": "파두, 제주반도체, 가온칩스",
    "반도체 전공정": (
        "주성엔지니어링, 원익IPS, HPSP, 피에스케이, 유진테크, 티씨케이, "
        "테스, 동진쎄미켐, 파크시스템스, 원익홀딩스, 코미코, 브이엠, "
        "하나머티리얼즈, 에스앤에스텍, 솔브레인홀딩스, GST, 에스티아이, "
        "오로스테크놀로지, 넥스틴, 에이치엠넥스, 디아이티, 케이씨텍, "
        "유니셈, 원익머티리얼즈, 이엔에프테크놀로지, 엘티씨, 원익QnC, "
        "한솔아이원스, 케이엔제이, 월덱스"
    ),
    "반도체 후공정": (
        "한미반도체, 이오테크닉스, 리노공업, ISC, 티에스이, 하나마이크론, "
        "고영, 테크윙, SFA반도체, 비아트론, 한화비전, 한화비젼, 피에스케이홀딩스, "
        "프로텍, 와이씨, 디아이, 두산테스나, 유니테스트, 네오셈, 펨트론, "
        "마이크로컨텍솔, 샘씨엔에스, 네패스아크, 한양디지텍, 덕산하이메탈"
    ),
    "기판": "삼성전기, 이수페타시스, 코리아써키트, 티엘비, 기가비스",
    "전력": "LS, LS ELECTRIC, 효성중공업, HD현대일렉트릭, HD일렉트릭, 가온전선, 산일전기, 대한전선",
    "원전": "두산에너빌리티, 한국전력, 한전기술, 우리기술, 비에이치아이",
    "에너지": "한화솔루션, SK이터닉스, HD현대에너지솔루션, SK오션플랜트, OCI홀딩스, 태웅",
    "방산·우주항공": (
        "한화시스템, 한화에어로스페이스, 한화에어로, 한국항공우주, "
        "LIG디펜스앤에어로스페이스, LIG디펜스, LIG넥스원, "
        "에이치브이엠, 에이치브이, 현대로템, 스피어"
    ),
    "조선": "HD현대중공업, HD한국조선해양, HD한국조선, 한화오션, 성광벤드",
    "IT서비스": "NAVER, 카카오, 현대오토에버, SK텔레콤, KT, LG유플러스",
    "광통신": "대한광통신, 오이솔루션, RFHIC, RF머트리얼즈",
    "로봇": "레인보우로보틱스, 로보티즈, 현대무벡스, 에스피지, SFA, 유일로보틱스, 삼현",
    "핀테크": "카카오페이, 삼성카드",
    "엔터": "하이브, 에스엠, JYP Ent., JYP Ent, 와이지엔터테인먼트",
    "게임": "크래프톤, 펄어비스, 카카오게임즈, 카카오게임",
    "바이오": (
        "SK바이오팜, 알테오젠, HLB, 에이비엘바이오, 펩트론, 리가켐바이오, "
        "보로노이, 올릭스, 메지온, 디앤디파마텍, 네이처셀, 에임드바이오, "
        "코오롱티슈진, 오스코텍, 오름테라퓨틱, 큐리언트"
    ),
    "제약": (
        "삼성바이오로직스, 셀트리온, 삼성에피스홀딩스, 한미약품, 유한양행, "
        "삼천당제약, 에스티팜, 셀트리온제약, HK이노엔"
    ),
    "의료기기·미용": "씨젠, 씨어스, 큐리옥스바이오, 리브스메드, 파마리서치, 휴젤",
    "금융": (
        "KB금융, 신한지주, 하나금융지주, 우리금융지주, 기업은행, "
        "카카오뱅크, JB금융지주, BNK금융지주, iM금융지주, 제주은행"
    ),
    "2차전지": (
        "삼성SDI, SK이노베이션, 포스코퓨처엠, LG에너지솔루션, "
        "삼화콘덴서, POSCO홀딩스, HT로보틱스, 삼기에너지솔루션즈, "
        "엘앤에프, 에코프로, LG화학, 에코프로비엠, 메가터치, "
        "포스코인터내셔널, 미코, 한솔케미칼, 인텍플러스, "
        "SK아이이테크놀로지, SK아이테크놀로지, "
        "고려아연, 필옵틱스, 대주전자재료, 알멕, 삼아알미늄, "
        "이수스페셜티케미컬, 코스모신소재, 에코프로머티, 대한유화, "
        "코세스, 롯데에너지머티리얼즈, 일진머티리얼즈, 피노, "
        "신성에스티, 신성델타테크, "
        "나노팀, 코칩, 엔켐, SKC, 포스코케미칼, 천보, 피엔티, "
        "코스모화학, 씨아이에스"
    ),
    "정유": "S-Oil, SK가스, E1, 흥구석유, 극동유화, 중앙에너비스",
    "화장품": (
        "에이피알, 아모레퍼시픽, LG생활건강, 한국콜마, 코스맥스, "
        "아모레퍼시픽홀딩스, 아모레G, 네오팜, 한국화장품제조, "
        "콜마홀딩스, 애경산업, 마녀공장"
    ),
}


def _build_name_map():
    result = {}
    for sector, names in SECTOR_STOCKS.items():
        for name in (item.strip() for item in names.split(",")):
            if not name or name in result:
                raise ValueError("Empty or duplicate sector override: " + name)
            result[name] = sector
    return result


NAME_SECTOR_OVERRIDES = _build_name_map()


def override_sector(name, current_sector):
    """Return the curated sector, or preserve the existing classification."""
    return NAME_SECTOR_OVERRIDES.get(str(name or "").strip(), current_sector)


def override_frame_sectors(frame):
    """Apply overrides to a copy of a daily-stock frame without touching other rows."""
    if frame.empty or not {"name", "sector"}.issubset(frame.columns):
        return frame
    result = frame.copy()
    overrides = result["name"].fillna("").astype(str).str.strip().map(NAME_SECTOR_OVERRIDES)
    result.loc[overrides.notna(), "sector"] = overrides[overrides.notna()]
    return result
