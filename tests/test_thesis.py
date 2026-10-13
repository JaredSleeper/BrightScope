from brightscope import thesis

OPTS = {"categories": ["Insurance", "Artificial Intelligence (AI)"], "countries": ["United States"]}


async def test_normalize_validates_and_maps(monkeypatch):
    async def fake_match(names):
        return ["Andreessen Horowitz"] if names else []

    monkeypatch.setattr(thesis, "_match_investors", fake_match)
    raw = {
        "keywords": ["insurance", " claims "],
        "categories": ["insurance", "Not a category"],
        "countries": ["USA"],
        "funds": ["a16z"],
        "scope": "elite_lead",
        "since": "2025-04-13",
        "until": "garbage",
        "min_round_m": 20,
        "min_hc_growth": True,
        "progress": "raised_since",
        "affinity": "nope",
        "sort": "relevance",
    }
    params = await thesis.normalize(raw, OPTS, "insurance led by a top fund")
    assert ["kw", "claims"] in params
    assert ["category", "Insurance"] in params
    assert ["category", "Not a category"] not in params
    assert ["country", "United States"] in params
    assert ["fund", "Andreessen Horowitz"] in params
    assert ["since", "2025-04-13"] in params
    assert not any(k == "until" for k, _ in params)
    assert ["min_round_m", "20"] in params
    assert not any(k == "min_hc_growth" for k, _ in params)
    assert not any(k == "affinity" for k, _ in params)
    assert ["sort", "relevance"] in params


async def test_elite_lead_dropped_unless_thesis_mentions_leading(monkeypatch):
    async def fake_match(names):
        return list(names)

    monkeypatch.setattr(thesis, "_match_investors", fake_match)
    raw = {"funds": ["Sequoia Capital"], "scope": "elite_lead"}
    backed = await thesis.normalize(raw, OPTS, "robotics backed by Sequoia")
    assert not any(k == "scope" for k, _ in backed)
    led = await thesis.normalize(raw, OPTS, "robotics rounds Sequoia led")
    assert ["scope", "elite_lead"] in led
