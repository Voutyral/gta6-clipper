"""Galerie : une version par clip, clips masqués."""
from clipper.gallery import keep_latest


def card(file: str, src: str) -> dict:
    return {"file": file + ".mp4", "source_url": src, "go": True}


def test_rerun_of_the_same_clip_keeps_only_the_latest_version():
    cards = [card("20261118-021952-mastersnakou-00", "u/heli"), card("20261118-030424-mastersnakou-00", "u/heli"),
             card("20261118-022550-mastersnakou-10", "u/autre")]
    assert [c["file"] for c in keep_latest(cards)] == ["20261118-030424-mastersnakou-00.mp4",
                                                       "20261118-022550-mastersnakou-10.mp4"]


def test_several_moments_from_one_run_all_stay():
    cards = [card("20261118-090000-vod-00", "u/vod"), card("20261118-090000-vod-01", "u/vod")]
    assert len(keep_latest(cards)) == 2


def test_hidden_clips_are_left_out():
    cards = [card("20261118-030954-suryanshexd-00", "u/k"), card("20261118-044650-owlfr-00", "u/o")]
    assert [c["file"] for c in keep_latest(cards, ["20261118-030954-suryanshexd-00"])] == ["20261118-044650-owlfr-00.mp4"]
