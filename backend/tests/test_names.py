"""gamma/names.py: one author string as every format that splits it reads
it, a name kept in two fields joined so it splits back the same way, and the
forms a Zotero sync and a reader compare and print a name in."""

from gamma import names


def test_a_suffix_reads_where_zotero_and_bibtex_keep_it():
    """Zotero keeps a suffix in the first name ("Martin Luther, Jr." /
    "King", joined "Martin Luther, Jr. King"), BibTeX after the surname's
    comma ("King, Jr., Martin Luther"): both are King, Martin Luther, Jr."""
    for name in ("Martin Luther, Jr. King", "King, Jr., Martin Luther", "Martin Luther King, Jr."):
        assert names.name_parts(name) == ("King", "Martin Luther", "Jr."), name
        assert names.split_name(name) == ("King, Jr.", "Martin Luther"), name
    assert names.name_parts("Robert F., Jr. Kennedy") == ("Kennedy", "Robert F.", "Jr.")


def test_a_surname_that_is_an_organisations_word_is_a_persons_before_a_comma_or_after_one_given_name():
    """Bureau, Office, Panel and Fund made no organisation's name before
    release 72: "Jean Bureau", as every name stored then reads, is a
    person's still; an organisation of more words keeps the new reading."""
    assert names.split_name("Bureau, Jean") == ("Bureau", "Jean")
    assert names.split_name("Jean Bureau") == ("Bureau", "Jean")
    assert names.split_name("Anna Fund") == ("Fund", "Anna")
    for org in ("U.S. Census Bureau", "International Labour Office", "International Monetary Fund",
                "Jean Collaboration", "Science Center", "University of California, Berkeley",
                "Bureau, International Labor Office"):
        assert names.split_name(org) == (org, ""), org


def test_a_name_in_two_fields_is_joined_to_split_back_as_its_source_split_it():
    cases = {
        ("Ada", "Lovelace"): "Ada Lovelace",
        ("Jorge Fernando", "Pereira Campos"): "Pereira Campos, Jorge Fernando",
        ("Floris", "Den Hengst"): "Den Hengst, Floris",
        ("Frank van", "Harmelen"): "Frank van Harmelen",  # Zotero's particle in the first name
        ("Martin Luther, Jr.", "King"): "King, Jr., Martin Luther",
        ("Jean", "Bureau"): "Jean Bureau",  # a person, as "Jean Bureau" reads
        ("Anna Maria", "Bureau"): "Bureau, Anna Maria",  # which "Anna Maria Bureau" would not read as
        ("Planck", "Collaboration"): "Planck Collaboration",  # an organisation in two fields stays one
        ("Jr.", "Pereira Campos"): "Jr. Pereira Campos",  # no form splits back: as it was
        ("Carrasco Kind,", "M."): "Carrasco Kind, M.",  # a comma of its own
        ("", "Plato"): "Plato",
    }
    assert {pair: names.join_name(*pair) for pair in cases} == cases
    # Whichever form it takes reads given name first as the fields joined do.
    for (given, family), joined in cases.items():
        assert names.given_first(joined) == names.given_first(f"{given} {family}".strip()), joined


def test_a_page_name_compares_as_the_item_it_makes_reads_back():
    """``sent_form``: the fields ``split_name`` writes, read back given
    name first, as a Zotero sync reads a creator (``given_first``)."""
    for name in ("Abbott, T. M. C.", "T. M. C. Abbott", "Lovelace, Ada", "Pereira Campos, Jorge Fernando",
                 "Smith, John, Esq.", "ATLAS Collaboration", "King, Jr., Martin Luther"):
        last, given = names.split_name(name)
        assert names.sent_form(name) == names.given_first(f"{given} {last}" if given else last), name
    assert names.sent_form("Abbott, T. M. C.") == names.sent_form("T. M. C. Abbott") == "T. M. C. Abbott"


def test_the_key_reads_a_joined_names_surname_and_any_other_name_as_before():
    """``joined_family``: the surname of a name join_name writes with a
    comma; None for any other, whose key keeps its last word."""
    assert names.joined_family("Pereira Campos, Jorge Fernando") == "Pereira Campos"
    assert names.joined_family("King, Jr., Martin Luther") == "King"
    assert names.joined_family("Bureau, Anna Maria") == "Bureau"
    for name in ("Lovelace, Ada", "Smith, J.", "Martin Luther King, Jr.", "Ada Lovelace", "ATLAS Collaboration",
                 "Bureau, Jean"):
        assert names.joined_family(name) is None, name


def test_a_list_of_names_reads_unambiguously_and_a_label_prints_the_surname():
    assert names.author_list(["Pereira Campos, Jorge Fernando", " Den Hengst, Floris ", ""]) == \
        "Pereira Campos, Jorge Fernando; Den Hengst, Floris"
    assert names.author_list(["A", "B", "C"], 2) == "A; B et al."
    assert names.author_list(["A", "B"], 2) == "A; B"
    assert [names.family_name(n) for n in ("Pereira Campos, Jorge Fernando", "Jan van der Berg", "Ada Lovelace",
                                           "ATLAS Collaboration")] == [
        "Pereira Campos", "van der Berg", "Lovelace", "ATLAS Collaboration"]
