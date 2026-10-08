from core.engines.ascii2d import Ascii2DEngine
from core.engines.saucenao import SauceNaoEngine
from core.engines.tracemoe import TraceMoeEngine
from core.models import Confidence


class Session:
    pass


def test_ascii2d_extracts_file_token_and_external_results():
    home = """
    <form action='/search/file' method='post'>
      <input value='token&amp;value' name='authenticity_token'>
    </form>
    """
    assert Ascii2DEngine._extract_file_token(home) == "token&value"

    page = """
    <div class='row item-box'>
      <img src='/thumbnail/query.jpg'>
      <div class='clearfix'></div>
    </div>
    <div class='row item-box'>
      <img src='//images.example/thumb.jpg'>
      <h6><a href='https://www.pixiv.net/artworks/123'>Work &amp; Name</a></h6>
      <div class='clearfix'></div>
    </div>
    <div class='row item-box'>
      <h6><a href='https://www.pixiv.net/artworks/123'>Duplicate</a></h6>
      <div class='clearfix'></div>
    </div>
    """
    hits = Ascii2DEngine.parse_result_page(page)
    assert len(hits) == 1
    assert hits[0].engine == "Ascii2D"
    assert hits[0].title == "Work & Name"
    assert hits[0].work_url == "https://www.pixiv.net/artworks/123"
    assert hits[0].thumbnail_url == "https://images.example/thumb.jpg"
    assert hits[0].confidence is Confidence.POSSIBLE


def test_saucenao_parser_filters_low_confidence():
    engine = SauceNaoEngine(
        Session(),
        api_key="key",
        high_threshold=85,
        possible_threshold=70,
        hide=2,
    )
    result = engine.parse_response(
        {
            "header": {"status": 0, "short_remaining": 3, "long_remaining": 80},
            "results": [
                {
                    "header": {
                        "similarity": "94.5",
                        "thumbnail": "https://img.example/a.jpg",
                        "index_name": "Pixiv Images",
                    },
                    "data": {
                        "title": "Example",
                        "member_name": "Artist",
                        "pixiv_id": 123,
                        "material": ["Series A", "Series B"],
                        "characters": "Character A",
                        "ext_urls": ["https://www.pixiv.net/artworks/123"],
                    },
                },
                {
                    "header": {"similarity": "40", "index_name": "Pixiv Images"},
                    "data": {"title": "Noise"},
                },
            ],
        }
    )
    assert len(result.hits) == 1
    assert result.hits[0].confidence is Confidence.HIGH
    assert result.hits[0].creator == "Artist"
    assert result.hits[0].material == "Series A, Series B"
    assert result.hits[0].characters == "Character A"
    assert result.hits[0].work_url == "https://www.pixiv.net/artworks/123"


def test_saucenao_uses_work_source_and_ignores_external_source_pages():
    engine = SauceNaoEngine(
        Session(),
        api_key="key",
        high_threshold=85,
        possible_threshold=70,
        hide=2,
    )
    result = engine.parse_response(
        {
            "header": {"status": 0},
            "results": [
                {
                    "header": {
                        "similarity": "89.9",
                        "index_name": "Danbooru",
                    },
                    "data": {
                        "source": "https://i.pximg.net/img-original/example.jpg",
                        "member_name": "zhudouzi",
                        "ext_urls": ["https://danbooru.donmai.us/posts/12109343"],
                    },
                }
            ],
        }
    )
    hit = result.hits[0]
    assert hit.title == "Danbooru"
    assert hit.work_url == "https://i.pximg.net/img-original/example.jpg"
    assert "danbooru.donmai.us" not in hit.work_url


def test_tracemoe_parser_marks_adult_and_time():
    engine = TraceMoeEngine(
        Session(), api_key="", high_threshold=90, possible_threshold=87
    )
    result = engine.parse_response(
        {
            "frameCount": 120,
            "result": [
                {
                    "anilist": {
                        "id": 20,
                        "title": {"native": "作品名"},
                        "isAdult": True,
                    },
                    "episode": 3,
                    "from": 62.5,
                    "to": 64.0,
                    "similarity": 0.93,
                    "image": "https://media.trace.moe/image.jpg",
                }
            ],
        }
    )
    hit = result.hits[0]
    assert hit.confidence is Confidence.HIGH
    assert hit.adult is True
    assert hit.from_seconds == 62.5
