# SPDX-License-Identifier: GPL-2.0
"""What a search shows, apart from how it is drawn: from what each source
found so far, the sections of results and the line above them.

    view(kind, query, sources, found, loading, failed, sort)
      kind      "search" (videos) or "channels"
      sources   [(id, label)] in the order of the Scope, YouTube first
      found     {id: list, or None while nothing came}
      loading   ids still being asked; failed: ids whose asking failed
      sort      "date" or "relevance" (videos only)
    -> {"sections": [{"label": label or None, "items": [...]}],
        "note": the line to show ("" for none),
        "can_sort": whether the newest/relevance switch is offered}

Videos by date: what all sources found together, newest first, in one
section (the sources find by relevance; asked for the newest, they bring
the newest of anything, matching or not). By relevance, and channels
always: a section per source, labelled when there are several.
"""


def view(kind, query, sources, found, loading, failed, sort):
    labels = [label for _sid, label in sources]
    waiting = [label for sid, label in sources if found.get(sid) is None and sid in loading]
    broken = [label for sid, label in sources if found.get(sid) is None and sid in failed]
    any_found = any(found.get(sid) for sid, _label in sources)
    sections = []
    if kind == "search" and sort == "date":
        items = [v for sid, _label in sources for v in found.get(sid) or []]
        items.sort(key=lambda v: v.get("published") or 0, reverse=True)
        if items:
            sections.append({"label": None, "items": items})
    else:
        for sid, label in sources:
            if found.get(sid):
                sections.append({"label": label if len(labels) > 1 else None,
                                 "items": found[sid]})
    notes = []
    if waiting:
        notes.append("Ищу «%s» (%s)…" % (query, ", ".join(waiting)))
    if broken:
        notes.append("Не удалось: %s — нет связи?" % ", ".join(broken))
    if not waiting and not broken and not any_found:
        notes.append("Ничего не найдено." if kind == "search" else "Каналы не найдены.")
    return {"sections": sections, "note": " ".join(notes),
            "can_sort": kind == "search" and any_found}
