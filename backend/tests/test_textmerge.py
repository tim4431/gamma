"""The same-block text merge (gamma/textmerge.py): two changes made from one
base are applied together in base coordinates, so concurrent typing at the
same or neighbouring offsets keeps both people's text, and only a span both
sides replaced falls back to the text already stored."""

from conftest import make_page
from gamma import textmerge


def test_semantic_hunks_keep_two_rewrites_of_one_sentence_apart():
    # Two rewrites of one phrase, merged by characters, interleave the few
    # letters they share; as words they clash, and the stored text stands.
    base, ours, theirs = "one two three. tail", "my own sentence. tail", "the AI's sentence. tail"
    assert textmerge.merge(base, ours, theirs, semantic=True) == ("the AI's sentence. tail", False)
    assert textmerge.merge(base, ours, theirs)[0] != theirs
    # Words elsewhere still merge.
    assert textmerge.merge("one two three. tail", "one two three. tail, and more", "the AI's sentence. tail",
                           semantic=True) == ("the AI's sentence. tail, and more", True)


def test_edits_to_different_spans_both_survive():
    assert textmerge.merge("hello world", "hello brave world", "hello world!") == ("hello brave world!", True)
    assert textmerge.merge("alpha beta gamma delta", "ALPHA beta gamma delta",
                           "alpha beta gamma DELTA") == ("ALPHA beta gamma DELTA", True)
    assert textmerge.merge("The cat sat", "The dog sat", "The cat sat down") == ("The dog sat down", True)


def test_insertions_at_one_caret_keep_both_the_stored_one_first():
    assert textmerge.merge("tag", "tagY", "tagx") == ("tagxY", True)
    assert textmerge.merge("tag", "Ytag", "xtag") == ("xYtag", True)


def test_typing_on_both_sides_of_a_merged_caret_keeps_every_keystroke():
    # One person typed x, the other Y at the end of the same block; the merge
    # made "tagxY". Then one types nine more x's after the x (before the Y),
    # the other nine more Y's at the very end, both from "tagxY".
    base = "tagxY"
    stored = "tag" + "x" * 10 + "Y"
    text, clean = textmerge.merge(base, base + "Y" * 9, stored)
    assert clean and text == "tag" + "x" * 10 + "Y" * 10


def test_an_insertion_in_front_of_a_replaced_word_stays_in_front_of_it():
    # one side typed a word before "beta", the other replaced "beta": the
    # typed word stays where it was typed, not glued to the replacement
    assert textmerge.merge("alpha beta", "alpha new beta", "alpha BETA") == ("alpha new BETA", True)
    assert textmerge.merge("alpha beta", "alpha BETA", "alpha new beta") == ("alpha new BETA", True)


def test_an_insertion_inside_a_span_the_other_side_deleted_survives():
    assert textmerge.merge("hello world", "hello", "hello wor!ld") == ("hello!", True)
    assert textmerge.merge("hello world", "hello wor!ld", "hello") == ("hello!", True)


def test_an_insertion_beside_text_it_repeats_is_not_turned_around():
    # "cool " typed in front of "cat" is also "ool c" typed after its "c";
    # merged with a delete of "cat " that reading kept "ool c" and lost the
    # "c" typed in front of it: "the ool csat"
    assert textmerge.merge("the cat sat", "the cool cat sat", "the sat") == ("the cool sat", True)
    assert textmerge.merge("the cat sat", "the sat", "the cool cat sat") == ("the cool sat", True)
    assert textmerge.merge("we see the tree", "we see the tall tree", "we see the") == ("we see the tall", True)
    # a word typed in front of a deleted last word, which reads as " beta"
    assert textmerge.merge("alpha beta", "alpha TOKEN beta", "alpha") == ("alpha TOKEN", True)


def test_a_deletion_beside_text_it_repeats_is_read_around_the_other_insertion():
    # "alpha " and "omega " deleted in one go read as " omega alpha" up to
    # the end; the word the other side typed before the last "alpha" then
    # sat inside it and lost its space
    base = "R87CKLGD alpha alpha omega alpha"
    ours, theirs = "R87CKLGD alpha alpha", "R87CKLGD alpha alpha omega XWR8692R alpha"
    assert textmerge.merge(base, ours, theirs) == ("R87CKLGD alpha XWR8692R alpha", True)
    assert textmerge.merge(base, theirs, ours) == ("R87CKLGD alpha XWR8692R alpha", True)
    # a deleted word that reads as starting one letter in, inside the next
    # word's first letter, keeps a word typed in front of that next word whole
    base = "B3G49CWV DTS463RH DLDVJDRT X9TFFM8P"
    ours, theirs = "B3G49CWV DTS463RH JBVW2KD4 DLDVJDRT X9TFFM8P", "B3G49CWV DLDVJDRT X9TFFM8P"
    assert textmerge.merge(base, ours, theirs) == ("B3G49CWV JBVW2KD4 DLDVJDRT X9TFFM8P", True)


def test_tokens_typed_and_deleted_around_each_other_stay_whole():
    # the crowd soak's words (frontend/tests/e2e/collabSoak.mjs): a token
    # typed between two words the other side deleted, and one typed in
    # front of a deleted token
    assert textmerge.merge("zeta WSVQ5R6L theta RJBFVZZM", "theta RJBFVZZM",
                           "zeta WLXCVQTR WSVQ5R6L theta RJBFVZZM") == ("WLXCVQTR theta RJBFVZZM", True)
    assert textmerge.merge("delta DDQ4JDT7 WS3CP5DN FBNR5KTF lambda 7VP9QHHZ",
                           "delta DDQ4JDT7 WS3CP5DN FPRT65ST FBNR5KTF lambda",
                           "delta DDQ4JDT7 WS3CP5DN lambda 7VP9QHHZ") == ("delta DDQ4JDT7 WS3CP5DN FPRT65ST lambda", True)


def test_a_span_both_replaced_keeps_the_stored_text():
    assert textmerge.merge("The cat sat", "The dog sat", "The cow sat") == ("The cow sat", False)
    # the rest of the same change still applies
    assert textmerge.merge("The cat sat on it", "The dog sat on it!", "The cow sat on it") == ("The cow sat on it!", False)


def test_nothing_to_merge():
    assert textmerge.merge("a", "b", "a") == ("b", True)      # the block didn't move on: a plain replace
    assert textmerge.merge("a", "a", "c") == ("c", True)      # no change of ours
    assert textmerge.merge("a", "ab", "ab") == ("ab", True)   # already the text


def test_concurrent_typing_at_one_caret_through_the_op_endpoint(guest):
    page = make_page(guest, "Same caret page")
    blk = guest.post("/api/blocks", json={"parent_id": page["id"], "content": "tag"}).json()

    def put(client_id, content, base):
        r = guest.post(f"/api/pages/{page['id']}/ops", json={
            "client": client_id, "ops": [{"op": "set", "id": blk["id"], "content": content, "base": base}]})
        assert r.status_code == 200, r.text
        return r.json()["ops"][0]["content"]

    assert put("a", "tagx", "tag") == "tagx"
    merged = put("b", "tagY", "tag")
    assert merged == "tagxY"
    assert put("a", "tag" + "x" * 10 + "Y", merged) == "tag" + "x" * 10 + "Y"
    assert put("b", merged + "Y" * 9, merged) == "tag" + "x" * 10 + "Y" * 10
