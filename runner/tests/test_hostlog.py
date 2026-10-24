from runner import hostlog


def test_excerpt_starts_at_the_first_error_of_the_last_burst():
    log = ["boot %d" % i for i in range(50)] + [
        "Error: Exception at tick 9: bad key category",
        "stack traceback:",
        "  [C]: in function '__index'",
        "  mod.lua:19: in function <mod.lua:16>",
        "Info: state InGame -> Failed",
        "Quitting: multiplayer error.",
    ] + ["shutdown %d" % i for i in range(9)]
    ex = hostlog.excerpt("\n".join(log))
    assert "bad key category" in ex and "mod.lua:19" in ex and "shutdown 8" in ex
    assert "boot 10" not in ex


def test_evidence_puts_files_with_errors_first_and_skips_duplicates(tmp_path):
    (tmp_path / "a.log").write_text("ok\nFatal: it broke\nbye\n")
    (tmp_path / "b.log").write_text("ok\nFatal: it broke\nbye\n")        # same text as a.log
    (tmp_path / "list.json").write_text('{"x": 1}\n')
    text, names = hostlog.evidence([tmp_path], since=0)
    assert names[0] in ("a.log", "b.log") and len([n for n in names if n.endswith(".log")]) == 1
    assert names[-1] == "list.json" and "it broke" in text
