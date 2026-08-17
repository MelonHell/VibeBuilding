# Участок и происхождение — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Пайплайн измеряет весь участок, а не одно здание, и отчёт говорит, чем измерена каждая часть — так что часть, не измеренная ничем, краснеет вместо того, чтобы выглядеть зелёной.

**Architecture:** `sources.Evidence.answers("plan")` сейчас выбирает **одну** ветку на всё здание, и всё, чего эта ветка не покрывает, для обмера не существует. Заменяется сборкой по частям: карта отвечает за то, что рисует, захват — за остальное, объявление — за то, чего нет нигде; каждая часть несёт три поля происхождения (пятно, высота, свидетель), а гейт печатает таблицу покрытия и краснеет на части без источника. Отдельно: клип перестаёт резать ниже датума, и появляется строка, которая говорит, что постройка вышла за клип, вместе с коробкой, которой хватило бы.

**Tech Stack:** Python 3, без внешних зависимостей кроме Pillow; тесты — скрипты `tools/*_selftest.py`, запускаемые модулями (`python -m tools.pipeline_selftest`); pytest в проекте нет.

**Spec:** [docs/superpowers/specs/2026-08-17-site-scope-and-provenance-design.md](../specs/2026-08-17-site-scope-and-provenance-design.md)

## Global Constraints

- **Один блок — один метр, один пиксель карты — один метр.** Ни одного масштабного коэффициента нигде.
- **Библиотека импортируется абсолютно** (`from blockwright import ...`); здания — пакеты под `buildings/`, запускаются модулями из корня проекта.
- **Тестов на pytest нет.** Проверка — `tools/*_selftest.py`, запускаемые как модули. Новая проверка добавляется в существующий селфтест или заводится новым файлом того же вида.
- **Существующие шестнадцать зданий заморожены**: не чинятся, не переклипаются, не запускаются. `buildings/*` в `.gitignore` кроме `_template`. Эталон нового механизма — `buildings/_template`.
- **Совместимость со старыми зданиями не поддерживается.** Старая одноветочная выборка плана **удаляется**, а не сохраняется рядом.
- **Язык:** код, комментарии, докстринги и сообщения коммитов — английский, в стиле окружающего кода (объяснять *почему*, а не *что*). Документация в `docs/` — русский.
- **Ни одного числа без источника.** Новая константа либо приходит из `derived.json`, либо объявлена с записанной причиной.
- **Коммит после каждой задачи.**

**User decisions (already made):**
- «миграция не нужна, я не хочу тратить токены на фиксы уже готовых зданий» — существующие здания не приводятся к новому механизму.
- «заморозка» — старые здания после правок не запускаются, и одноветочная выборка удаляется, а не сохраняется как частный случай.
- «стадию 6 посадка вообще пока выпили, она не используется» — `placement_of`, `LAYOUT_SCHEM` и глава доков удаляются.
- «ты должен по layout.png определить размер, форму и угол, а то, чего нет на layout.png уже брать из остальных источников» — сборка плана по частям.
- «mesh-clip обычно обрезает территорию, мне кажется он не должен обрезать территорию и мы должны всегда работать со всей территорией» — клип по участку, не по зданию.

---

### Task 1: Выпилить стадию 6 — посадку в мировые координаты

**Goal:** `finish()` перестаёт принимать `layout`/`template` и вычислять мировой сдвиг; `LAYOUT_SCHEM` уходит из путей; доки перестают описывать стадию, которой нет ни у одного здания.

**Files:**
- Modify: `blockwright/finish.py` — удалить `placement_of`, параметры `layout`/`template`, поле `Finished.placement`
- Modify: `buildings/_template/paths.py` — удалить `LAYOUT_SCHEM` и его комментарий
- Modify: `buildings/_template/build.py:514-518` — убрать `template=`/`layout=` из вызова `finish()`
- Modify: `docs/pipeline.md` — удалить главу «Стадия 6 — посадка, необязательная»
- Modify: `docs/data-contract.md`, `.claude/commands/blockwright-new.md`, `.claude/skills/blockwright/SKILL.md` — убрать упоминания `layout.schem` и шестой стадии

**Acceptance Criteria:**
- [ ] `grep -rn "LAYOUT_SCHEM\|placement_of\|layout.schem" blockwright/ buildings/_template/ docs/ .claude/` ничего не находит
- [ ] `python -m tools.pipeline_selftest` зелёный на всех четырёх ветках
- [ ] `python -m tools.lint_buildings` не жалуется на `_template`

**Verify:** `python -m tools.pipeline_selftest` → последняя строка `all four kinds passed`

**Steps:**

- [ ] **Step 1: Убедиться, что стадия действительно мертва**

```bash
ls buildings/*/input/layout.schem 2>/dev/null | wc -l
```

Expected: `0` — ни у одного здания якоря нет. Если не ноль — остановиться и сказать об этом: спека исходила из нуля.

- [ ] **Step 2: Удалить `placement_of` и его вызов в `finish.py`**

Найти и удалить целиком функцию `placement_of`, поле `placement` в классе `Finished`, и блок в `finish()`:

```python
    if layout is not None and template is not None:
        done.placement, notes, said = placement_of(layout, template,
                                                   layout_blocks)
        done.notes.extend(notes)
        done.said.extend(said)
```

Из сигнатуры `finish()` убрать параметры `layout`, `template`, `layout_blocks`. Запись схематики становится:

```python
    done.schematic = out / f"{name}.schem"
    canvas.write(done.schematic)
```

- [ ] **Step 3: Убрать `LAYOUT_SCHEM` из шаблона**

В `buildings/_template/paths.py` удалить блок с комментарием и строкой:

```python
LAYOUT_SCHEM = INPUT / "layout.schem"
```

В `buildings/_template/build.py` вызов `finish()` становится:

```python
    done = finish(canvas, paths.OUT, site.frame,
                  schedule=SCHEDULE, orthos=paths.ORTHOS, scale=RENDER_SCALE,
                  plans=(("ground", site.ground),
                         ("upper", site.levels[len(site.levels) // 2])))
```

- [ ] **Step 4: Прогнать селфтест — он должен упасть, если что-то забыли**

```bash
python -m tools.pipeline_selftest
```

Expected: PASS. Падение с `TypeError: finish() got an unexpected keyword argument` означает, что вызов в шаблоне не поправлен.

- [ ] **Step 5: Вычистить доки**

Удалить из `docs/pipeline.md` главу `## Стадия 6 — посадка, необязательная` целиком. В `.claude/skills/blockwright/SKILL.md` убрать строку стадии 6 из таблицы стадий и абзац «Стадия 6 необязательна, и обычно её не делают». В `.claude/commands/blockwright-new.md` убрать строку `layout.schem` из таблицы входов и пункт «`layout.schem` нужен редко».

- [ ] **Step 6: Проверить, что ничего не осталось**

```bash
grep -rn "LAYOUT_SCHEM\|placement_of\|layout\.schem" blockwright/ buildings/_template/ docs/ .claude/ tools/
```

Expected: пусто.

- [ ] **Step 7: Коммит**

```bash
git add -A
git commit -m "Drop the world-siting stage nothing uses

No building has ever carried input/layout.schem, so finish() has been computing
a world offset from a file that is never there and printing a line about its
absence on every run. The pipeline ends at a schematic in local coordinates,
which is a finished result, and saying so in one place beats saying it in five."
```

---

### Task 2: Фикстура — пристройка, которой нет на карте

**Goal:** Синтетическое здание получает флигель, который есть в захвате и **не** нарисован на карте, — то есть дефект 009 в миниатюре. Новый селфтест `tools/coverage_selftest.py` документирует, что пайплайн о нём сейчас молчит.

**Files:**
- Modify: `tools/fixture.py` — `write_mesh` добавляет флигель; `write_map` его **не** рисует
- Create: `tools/coverage_selftest.py`
- Modify: `tools/pipeline_selftest.py` — добавить вызов нового селфтеста в конец

**Acceptance Criteria:**
- [ ] Меш фикстуры содержит второй объём 20×12 м высотой 6 м, отстоящий от главного корпуса на 15 м
- [ ] Карта фикстуры не изменилась: `write_map` рисует ровно то, что рисовала
- [ ] `python -m tools.coverage_selftest` **падает** с сообщением, что флигель не измерен ничем — это красный тест, который зелёным сделает задача 5
- [ ] `python -m tools.pipeline_selftest` остаётся зелёным (существующие проверки флигеля не видят, и это ровно тот факт, который документируется)

**Verify:** `python -m tools.coverage_selftest` → FAIL с текстом `outbuilding is in the capture and in no part list`

**Steps:**

- [ ] **Step 1: Прочитать, как фикстура пишет меш**

```bash
sed -n '107,150p' tools/fixture.py
```

Понадобится, чтобы добавить объём тем же способом, каким написаны существующие: флигель обязан быть такой же поверхностью-оболочкой, а не сплошным телом, иначе он не похож на фотограмметрию.

- [ ] **Step 2: Добавить флигель в меш фикстуры**

В `tools/fixture.py`, в `write_mesh`, после существующих объёмов:

```python
# An outbuilding the map does not draw. It is the whole point of
# `coverage_selftest`: a real site has a clubhouse, a pool house, a row of
# villas that the plan crop never showed, and until something says so they are
# built freehand and graded by nothing. Fifteen metres clear of the main block
# so that no closing operation joins the two into one mass.
OUTBUILDING = (0.0, 20.0, -27.0, -15.0, 6.0)   # u0, u1, v0, v1, top
```

и внутри `write_mesh`, тем же вызовом, каким пишутся стены главного корпуса:

```python
    u0, u1, v0, v1, top = OUTBUILDING
    box(handle, u0, u1, v0, v1, 0.0, top)
```

(имя `box` — та функция, которой `write_mesh` уже пишет объёмы; если она называется иначе, использовать существующее имя, а не заводить новое.)

- [ ] **Step 3: Написать красный селфтест**

Create `tools/coverage_selftest.py`:

```python
"""Every part the build puts up is measured by something -- or said not to be.

The failure this exists for is not a wrong number. It is a part nobody measured
at all, standing in a report that reads exactly like a measured one: on the
building this was written after, `part villas: 842 of 842 cells` printed green
beside `north_tower stands where the plan says: the two overlap 0.910`, and the
first compares the build against its own declaration while the second compares
it against a measured plan. Nothing in the file told them apart.

The fixture's outbuilding is that case in miniature: it stands in the capture
and the map never drew it. A run that says nothing about it is the run this
guards against.

    python -m tools.coverage_selftest
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIND = "_selftest_coverage"


def build(kind: str) -> Path:
    """Make the fixture building and measure it. Returns its out/ directory."""
    where = ROOT / "buildings" / kind
    subprocess.run([sys.executable, "-m", "tools.fixture", str(where)],
                   cwd=ROOT, check=True)
    subprocess.run([sys.executable, "-m", f"buildings.{kind}.probes.derive"],
                   cwd=ROOT, check=True)
    return where / "out"


def main() -> int:
    out = build(KIND)
    derived = json.loads((out / "derived.json").read_text(encoding="utf-8"))
    parts = [p["name"] for p in derived.get("parts", [])]

    # The outbuilding is 20 x 12 m, which nothing the map drew comes near. If no
    # part covers that patch, the survey does not know it exists.
    covered = [p for p in derived.get("parts", [])
               if p.get("v1", 0) < -10.0]
    if not covered:
        print(f"FAIL: outbuilding is in the capture and in no part list; "
              f"the survey found {len(parts)} part(s): {', '.join(parts)}")
        return 1

    missing = [p for p in derived["parts"]
               if not p.get("provenance", {}).get("plan")]
    if missing:
        print("FAIL: parts with no stated provenance: "
              + ", ".join(p["name"] for p in missing))
        return 1

    print(f"all {len(parts)} part(s) measured by something")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Прогнать — он обязан упасть**

```bash
python -m tools.coverage_selftest
```

Expected: `FAIL: outbuilding is in the capture and in no part list; the survey found 3 part(s): ...` и код возврата 1. Это красный тест задач 3 и 5.

- [ ] **Step 5: Убедиться, что старый селфтест не сломался**

```bash
python -m tools.pipeline_selftest
```

Expected: PASS. Флигель существующие проверки не трогает — они его не видят, и это и есть дыра.

- [ ] **Step 6: Коммит**

```bash
git add tools/fixture.py tools/coverage_selftest.py
git commit -m "Give the fixture a building the map never drew

The synthetic site had exactly the parts its map draws, so every branch of the
survey was tested against inputs that agree by construction. Real sites do not:
a clubhouse, a pool house and a row of villas stand on the one this was written
after, none of them on the crop, all of them built freehand and graded by
nothing.

The new selftest fails, deliberately. It is the red for the two tasks that give
each part a stated provenance and assemble the plan from more than one source."
```

---

### Task 3: Происхождение у каждой части

**Goal:** Каждая часть в `derived.json` несёт `provenance` — чем измерено пятно, чем измерена высота, есть ли свидетель, — и `derive` печатает таблицу покрытия.

**Files:**
- Modify: `blockwright/survey.py` — `Read` получает поле `provenance`; `Survey.main` пишет его в `out["parts"]`; `Survey.report` печатает таблицу
- Modify: `tools/coverage_selftest.py` — вторая половина проверки (`provenance`) начинает что-то значить

**Acceptance Criteria:**
- [ ] У каждой записи в `derived.json["parts"]` есть ключ `provenance` с тремя полями: `plan`, `height`, `witness`
- [ ] `plan` — одно из `map`, `vector`, `capture`, `model`, `declared`
- [ ] `height` — одно из `capture`, `model`, `declared`, `none`
- [ ] `witness` — `section` или `none`
- [ ] `derive` печатает таблицу покрытия: строка на часть, четыре колонки
- [ ] `python -m tools.pipeline_selftest` зелёный на всех четырёх ветках

**Verify:** `python -m tools.pipeline_selftest` → PASS, и `python -m buildings._selftest_mapped.probes.derive | grep -A6 "coverage"` печатает по строке на часть

**Steps:**

- [ ] **Step 1: Добавить поле в `Read`**

В `blockwright/survey.py`, класс `Read`, расширить `__slots__` и `__init__`:

```python
    __slots__ = ("source", "frame", "named", "order", "massing", "mass",
                 "slope", "unlatticed", "provenance")

    def __init__(self, source, frame, named, order, mass=None, massing=None,
                 provenance=None):
        ...
        # Where each part's footprint came from, by name. One branch fills this
        # with its own kind for every part; the assembled plan (see
        # `plan_of`) fills it per part, which is the whole reason it exists.
        self.provenance = provenance or {}
```

и в каждой из четырёх веток `from_vector` / `from_map` / `from_model` / `from_declared` проставить свой источник на все части:

```python
        return Read(source, frame, named, order, mass=template,
                    provenance={name: source.name for name in order})
```

(`from_declared` пишет `"declared"`, а не `source.name`: источник там — бриф или эскиз, а механизм — объявление, и в таблице покрытия важен механизм.)

- [ ] **Step 2: Собрать три поля в `Survey.main`**

В `Survey.main`, где формируется `out["parts"]`, добавить `provenance` каждой записи. Высота известна из `out["skyline"]`, свидетель — из того, попала ли часть в станции сечения:

```python
    def provenance_of(self, out: dict, read: Read) -> None:
        """Three words per part: what measured its footprint, its height, and
        what can contradict either.

        Written for the reader rather than for the code: everything here is
        already knowable from other keys in the file, and nobody ever worked it
        out, so a part built freehand read exactly like a part set out on a
        survey. Stating it costs a dictionary and removes the whole class.
        """
        skyline = out.get("skyline", {})
        graded = {name for name in out.get("sections", {})}
        for record in out["parts"]:
            name = record["name"]
            entry = skyline.get(name)
            if isinstance(entry, dict) and entry.get("median") is not None:
                height = entry.get("by") or "capture"
            elif name in (self.t.DECLARED_HEIGHTS or {}):
                height = "declared"
            else:
                height = "none"
            record["provenance"] = {
                "plan": read.provenance.get(name, "declared"),
                "height": height,
                "witness": "section" if name in graded else "none",
            }
```

и вызвать её в `main` сразу после `skyline_of`.

- [ ] **Step 3: Напечатать таблицу покрытия в `Survey.report`**

В `Survey.report`, после существующих строк про источники:

```python
        out_lines.append("")
        out_lines.append("coverage: what measured each part")
        out_lines.append(f"  {'part':22s} {'footprint':10s} {'height':10s} "
                         f"witness")
        for record in out.get("parts", []):
            p = record.get("provenance", {})
            out_lines.append(
                f"  {record['name']:22s} {p.get('plan', '-'):10s} "
                f"{p.get('height', '-'):10s} {p.get('witness', '-')}")
```

(имя аккумулятора строк — то, которым `report` уже пользуется; не заводить второе.)

- [ ] **Step 4: Прогнать**

```bash
python -m tools.pipeline_selftest
```

Expected: PASS на всех четырёх ветках.

```bash
python -m tools.fixture buildings/_scratch && python -m buildings._scratch.probes.derive | sed -n '/coverage/,/^$/p'
```

Expected: таблица с четырьмя колонками и строкой на каждую часть. Убрать за собой: `rm -rf buildings/_scratch`.

- [ ] **Step 5: Коммит**

```bash
git add blockwright/survey.py
git commit -m "State what measured each part, in the file and in the printout

Everything here was already derivable from other keys in derived.json, and
nobody ever derived it, so a part built freehand printed exactly like a part set
out on a survey -- same green, same shape of row. Three words per part and a
table under the survey costs a dictionary and removes the whole class of
mistake."
```

---

### Task 4: Строка гейта «каждая часть чем-то измерена»

**Goal:** Гейт краснеет, когда постройка объявила часть, у которой во всех трёх колонках происхождения прочерк.

**Files:**
- Modify: `blockwright/grading.py` — новый метод `coverage`, вызов из `main`
- Modify: `buildings/_template/gate.py` — при необходимости пробросить настройку
- Modify: `tools/coverage_selftest.py` — добавить проверку, что строка есть в отчёте

**Acceptance Criteria:**
- [ ] `report.json` содержит проверку `every part is measured by something`
- [ ] Проверка красная, если хоть одна **объявленная сборкой** часть не имеет ни измеренного пятна, ни измеренной высоты, ни свидетеля
- [ ] Проверка `[----]` с перечислением, если такие части есть, но здание объявило их через `UNMEASURED` с причиной
- [ ] Проверка зелёная, когда таких частей нет
- [ ] `python -m tools.pipeline_selftest` зелёный

**Verify:** `python -m tools.pipeline_selftest` → PASS; `python -m tools.coverage_selftest` доходит до второй проверки

**Steps:**

- [ ] **Step 1: Написать метод в `grading.py`**

Рядом с `witnesses` (строка ~1867):

```python
    def coverage(self, g, derived, sched):
        """Parts the build put up that nothing measured.

        The row this pipeline did not have. A declaration check asks whether
        blocks stand inside a mask the build itself drew, which is the build
        marking its own homework; this asks whether anything outside the build
        ever stated where that part goes or how tall it is. On the building this
        was written after, nine parts of fourteen would have failed here, and
        every one of them printed green.

        `UNMEASURED` is the building's own list of parts it knows nothing
        measured -- a phrase per part, not a flag. Named there, the row goes
        ungraded with the reasons printed rather than failing: a part standing
        on a photograph alone is a legitimate way to build and an illegitimate
        thing to report as checked.
        """
        stated = {r["name"]: r.get("provenance", {})
                  for r in derived.get("parts", [])}
        excused = self._("UNMEASURED", {}) or {}
        blind, named = [], []
        for name in sorted(sched.by_name):
            p = stated.get(name)
            if p is None:
                # Not a plan part at all -- a floor, a parapet, glazing. Those
                # are declared against a plan part and graded by `placement`.
                continue
            if p.get("plan") in ("map", "vector", "model", "capture"):
                continue
            if p.get("height") in ("capture", "model"):
                continue
            if p.get("witness") == "section":
                continue
            (named if name in excused else blind).append(name)

        if blind:
            g.add("every part is measured by something", False,
                  f"{len(blind)} part(s) have no measured footprint, no "
                  f"measured height and no witness: {', '.join(blind)}. Either "
                  "widen the clip so the reference covers them, declare their "
                  "sizes with the sheet or the phrase they came from, or name "
                  "them in UNMEASURED with the reason they cannot be measured.")
            return
        if named:
            g.ungraded("every part is measured by something",
                       "; ".join(f"{n}: {excused[n]}" for n in named))
            return
        g.add("every part is measured by something", True,
              f"all {len(stated)} plan part(s) carry a measured footprint, a "
              "measured height or a witness")
```

- [ ] **Step 2: Вызвать её из `Grading.main`**

Рядом с вызовом `self.witnesses(g, derived)`:

```python
        self.coverage(g, derived, sched)
```

- [ ] **Step 3: Добавить `UNMEASURED` в скелет гейта**

В `buildings/_template/gate.py`, рядом с прочими таблицами:

```python
# Parts nothing supplied can measure, each with the reason -- a phrase, not a
# flag. A part named here turns `every part is measured by something` from red
# to ungraded, which is the honest state: it was built on a photograph, and a
# photograph settles that a thing exists and settles no dimension of it.
#
# Naming a part here is a decision that costs a line. Leaving it out when
# nothing measures it is a decision that costs nothing and reads as a pass.
UNMEASURED: dict[str, str] = {
    # "pool_house": "photos/A11795157_35.jpeg shows it; the capture's clip
    #                stops twelve metres short of it",
}
```

- [ ] **Step 4: Прогнать селфтест**

```bash
python -m tools.pipeline_selftest
```

Expected: PASS. Фикстура строит только те части, которые нарисовала карта, поэтому строка зелёная.

- [ ] **Step 5: Проверить, что строка краснеет**

```bash
python -m tools.fixture buildings/_scratch
python -m buildings._scratch.probes.derive
python -m buildings._scratch.build
python -m buildings._scratch.gate | grep "every part is measured"
```

Expected: `[pass] every part is measured by something: all N plan part(s) carry ...`

Затем вручную добавить в `buildings/_scratch/build.py` объявление части, которой нет в плане, и убедиться, что строка стала `[FAIL]`. Убрать за собой: `rm -rf buildings/_scratch`.

- [ ] **Step 6: Коммит**

```bash
git add blockwright/grading.py buildings/_template/gate.py
git commit -m "Fail on a part nothing measured, instead of passing it

The declaration rows ask whether blocks stand inside a mask the build drew,
which is the build marking its own homework. Nothing asked whether anything
outside the build ever said where that part goes or how tall it is, so nine
parts of fourteen on one real building printed green while standing on numbers
typed off a photograph.

UNMEASURED is how a building says a part really cannot be measured. It takes a
phrase, and it turns the row ungraded rather than green: built on a photograph
is a legitimate way to build and an illegitimate thing to report as checked."
```

---

### Task 5: Сборка плана по частям

**Goal:** `plan_of` перестаёт выбирать одну ветку и собирает план из нескольких: карта отвечает за то, что рисует, захват — за остальное. Одноветочная выборка удаляется.

**Files:**
- Modify: `blockwright/survey.py` — новый метод `assemble`, переписанный `plan_of`
- Modify: `buildings/_template/probes/derive.py` — новая таблица `CAPTURE_PARTS`, новая константа `MATCH_FLOOR`
- Modify: `tools/coverage_selftest.py` — должен позеленеть

**Acceptance Criteria:**
- [ ] Часть, которую рисует карта, берёт форму, размер и угол **с карты**
- [ ] Часть, которой карта не рисует, но которую покрывает захват, становится частью с `provenance.plan == "capture"`
- [ ] Сопоставление идёт по IoU с порогом `MATCH_FLOOR` (умолчание 0.30); несопоставленная часть захвата называется в выводе, а не тихо становится новой
- [ ] Части захвата именуются через `CAPTURE_PARTS`; пустая таблица даёт `capture-1`, `capture-2` и печатает их размеры
- [ ] `python -m tools.coverage_selftest` **зелёный**
- [ ] `python -m tools.pipeline_selftest` зелёный на всех четырёх ветках

**Verify:** `python -m tools.coverage_selftest` → `all N part(s) measured by something`

**Steps:**

- [ ] **Step 1: Написать сборку**

В `blockwright/survey.py`, рядом с ветками:

```python
    def assemble(self, out: dict, read: Read, evidence) -> Read:
        """The plan the drawn source states, plus what only the reference holds.

        Four branches used to be four answers, one of which won. That is right
        for the question "which source states the plan" and wrong for the site:
        a crop shows the building somebody cropped it around, and the clubhouse,
        the pool house and the row of villas beside it are on no crop at all.
        Built anyway, they arrived with no footprint, no height and no witness,
        and the report could not tell them from the tower.

        So the drawn source keeps every part it draws -- it is a drawing made to
        be measured, and cleaner than photogrammetry -- and the reference
        contributes the parts it does not. Overlap decides which is which.
        """
        reference = evidence.reference
        if reference is None or read.source is reference:
            return read

        extra = model3d.read(reference.path, up=self.t.MODEL_UP,
                             scale=self.t.MODEL_SCALE)
        drawn = [read.named[n] for n in read.order]
        floor = self.t.MATCH_FLOOR

        loose, matched = [], []
        for piece in extra.parts:
            best = max((self.overlap(piece.mask, d.mask) for d in drawn),
                       default=0.0)
            (matched if best >= floor else loose).append((piece, best))

        out["assembly"] = {
            "drawn": len(drawn),
            "reference_parts": len(extra.parts),
            "matched": len(matched),
            "extra": len(loose),
            "floor": floor,
        }
        if not loose:
            return read

        names = list(self.t.CAPTURE_PARTS) or [
            f"capture-{i + 1}" for i in range(len(loose))]
        if len(names) != len(loose):
            raise SystemExit(
                f"the reference holds {len(loose)} part(s) the plan does not "
                f"draw, and CAPTURE_PARTS names {len(names)}.\n"
                + "".join(f"    {p!r} (best overlap {b:.2f})\n"
                          for p, b in loose)
                + "Name them all in the order printed, or empty the table to "
                  "have them called capture-1 and so on. A partial list would "
                  "put the wrong name on the wrong part.")

        named = dict(read.named)
        order = list(read.order)
        provenance = dict(read.provenance)
        for name, (piece, _) in zip(names, loose):
            named[name] = piece
            order.append(name)
            provenance[name] = "capture"

        joined = Read(read.source, read.frame, named, order,
                      mass=read.mass, massing=read.massing,
                      provenance=provenance)
        joined.slope, joined.unlatticed = read.slope, read.unlatticed
        return joined

    @staticmethod
    def overlap(a, b) -> float:
        """Intersection over union of two masks, 0.0 when either is empty."""
        both = (a & b).count()
        if not both:
            return 0.0
        return both / float((a | b).count())
```

- [ ] **Step 2: Переписать `plan_of`, удалив одноветочный выбор как единственный ответ**

```python
    def plan_of(self, out: dict | None = None) -> Read:
        """The plan: what the drawn source states, and what only the reference
        holds.

        `build.py`, `gate.py` and `review.py` all call this rather than reading a
        plan of their own, because the two have to agree cell for cell.

        The branch below still decides who states the *drawn* plan -- a vector
        beats a map beats a model beats a declaration, and that ranking is
        `sources.PREFER`. What changed is that its answer is no longer the whole
        plan: `assemble` adds the parts of the site the drawn source never drew.
        """
        out = {} if out is None else out
        evidence = sources.survey(self.paths)
        by = evidence.answers("plan")
        if by is None:
            raise SystemExit(sources.refuse(evidence) or "nothing states the plan")
        if by.name == "vector":
            read = self.from_vector(out, by)
        elif by.name == "map":
            read = self.from_map(out, by)
        elif by.name in ("model", "capture"):
            read = self.from_model(out, by)
        else:
            read = self.from_declared(out, by)
        return self.on_a_lattice(self.assemble(out, read, evidence))
```

- [ ] **Step 3: Добавить таблицы в скелет пробника**

В `buildings/_template/probes/derive.py`, рядом с `MODEL_PARTS`:

```python
# Names for the parts the reference holds and the drawn plan does not: a
# clubhouse the crop stopped short of, a pool house, a row of villas across the
# lawn. Largest first, the order `derive` prints them in.
#
# Empty is the right starting point, the same as everywhere else here: nobody
# knows how many such parts a site has until the reference has been read once.
CAPTURE_PARTS: tuple[str, ...] = ()

# How much two footprints must overlap to be the same part. Intersection over
# union, so 0.30 is a loose match on purpose: a map's outline and a capture's
# silhouette of one building routinely sit at 0.5-0.7, and anything that shares
# less than a third of its area with everything drawn is a different building.
#
# Raise it where the map and the capture agree closely and small neighbours keep
# being absorbed; lower it where a part keeps arriving twice.
MATCH_FLOOR = 0.30
```

- [ ] **Step 4: Прогнать красный тест — он обязан позеленеть**

```bash
python -m tools.coverage_selftest
```

Expected: `all 4 part(s) measured by something` и код возврата 0. Флигель фикстуры теперь приезжает как `capture-1`.

- [ ] **Step 5: Убедиться, что остальные ветки целы**

```bash
python -m tools.pipeline_selftest
```

Expected: PASS на всех четырёх. Ветка `described` не имеет эталона вовсе, поэтому `assemble` возвращает план как есть.

- [ ] **Step 6: Коммит**

```bash
git add blockwright/survey.py buildings/_template/probes/derive.py tools/coverage_selftest.py
git commit -m "Assemble the plan from every source that answers for a part

One branch won the plan question and everything outside its reach stopped
existing for the survey. That is right for a building and wrong for a site: a
crop shows what somebody cropped it around, and the clubhouse, the pool house
and the row of villas beside it are on no crop at all. They were built anyway,
with no footprint, no height and no witness, and nothing in the report told them
from the tower.

The drawn source keeps every part it draws, being a drawing made to be measured.
The reference contributes the parts it does not, matched by overlap so that the
same building does not arrive twice, and named through CAPTURE_PARTS the same
way every other part list is named -- empty first, filled after the first run
prints what it found."
```

---

### Task 6: Пороги мусора и отчёт об отброшенном

**Goal:** Разложение всего участка отсекает деревья, машины и навесы порогами по площади и высоте — и **печатает, сколько и какой площади** отбросило.

**Files:**
- Modify: `blockwright/survey.py` — `assemble` фильтрует и считает
- Modify: `buildings/_template/probes/derive.py` — `PART_LEAST_AREA`, `PART_LEAST_HEIGHT`

**Acceptance Criteria:**
- [ ] Часть эталона площадью меньше `PART_LEAST_AREA` не становится частью плана
- [ ] Часть эталона ниже `PART_LEAST_HEIGHT` не становится частью плана
- [ ] `derive` печатает строку: сколько кусков отброшено и какой суммарной площади
- [ ] `derived.json["assembly"]` несёт `dropped` — счёт и площадь
- [ ] `python -m tools.coverage_selftest` и `python -m tools.pipeline_selftest` зелёные

**Verify:** `python -m tools.coverage_selftest` → PASS, и вывод `derive` содержит строку `dropped`

**Steps:**

- [ ] **Step 1: Добавить константы в скелет**

```python
# What is too small or too low to be a part of the building rather than a thing
# standing on it. The reference is a capture of a whole site: it holds trees,
# cars, awnings and street furniture, and decomposing it whole brings them all.
#
# Both are stated rather than guessed, and both are printed with what they threw
# away. A threshold that quietly ate a wing reads exactly like one that quietly
# ate a hedge, and the only defence is the count.
PART_LEAST_AREA = 40.0        # square metres
PART_LEAST_HEIGHT = 2.5       # metres above the datum
```

- [ ] **Step 2: Отфильтровать в `assemble`, считая отброшенное**

В методе `assemble`, между разбиением на `loose`/`matched` и именованием:

```python
        kept, dropped = [], []
        for piece, best in loose:
            area = piece.mask.count()
            top = extra.tops[extra.parts.index(piece)]
            if area < self.t.PART_LEAST_AREA or top < self.t.PART_LEAST_HEIGHT:
                dropped.append((piece, area, top))
            else:
                kept.append((piece, best))
        loose = kept

        out["assembly"]["dropped"] = {
            "count": len(dropped),
            "area": round(sum(a for _, a, _ in dropped), 1),
            "tallest": round(max((t for _, _, t in dropped), default=0.0), 1),
        }
```

- [ ] **Step 3: Напечатать это в `Survey.report`**

Рядом с таблицей покрытия:

```python
        assembly = out.get("assembly")
        if assembly:
            out_lines.append(
                f"  assembled: {assembly['drawn']} drawn part(s), "
                f"{assembly['extra']} from the reference "
                f"(overlap floor {assembly['floor']:.2f})")
            gone = assembly.get("dropped", {})
            if gone.get("count"):
                out_lines.append(
                    f"  dropped:   {gone['count']} piece(s), {gone['area']} m2 "
                    f"in all, tallest {gone['tallest']} m -- under "
                    f"PART_LEAST_AREA or PART_LEAST_HEIGHT")
```

- [ ] **Step 4: Прогнать**

```bash
python -m tools.coverage_selftest && python -m tools.pipeline_selftest
```

Expected: оба PASS. Флигель фикстуры — 240 м² и 6 м, выше обоих порогов, поэтому уцелел.

- [ ] **Step 5: Проверить, что порог реально режет**

Временно поднять `PART_LEAST_AREA` до `400.0` в `buildings/_template/probes/derive.py`, прогнать `python -m tools.coverage_selftest` — он обязан упасть, а вывод `derive` — напечатать `dropped: 1 piece(s)`. Вернуть значение обратно.

- [ ] **Step 6: Коммит**

```bash
git add blockwright/survey.py buildings/_template/probes/derive.py
git commit -m "Say what the part thresholds threw away

Decomposing a whole site instead of one building brings the trees, the cars, the
awnings and the street furniture with it, so there have to be thresholds. A
threshold that quietly ate a wing reads exactly like one that quietly ate a
hedge, and the only defence against confusing them is the count -- so the run
prints how many pieces went, how much area in all, and how tall the tallest one
was."
```

---

### Task 7: Клип держит участок — запрет резать ниже датума и строка гейта

**Goal:** `ge_convert` отказывается резать ниже датума, а гейт печатает строку, которая говорит, что постройка вышла за клип, — вместе с коробкой, которой хватило бы.

**Files:**
- Modify: `tools/ge_convert.py` — `--clip-up` принимает только верхнюю границу; нижняя всегда открыта
- Modify: `blockwright/grading.py` — новый метод `site_covered`, вызов из `main`
- Modify: `.claude/skills/blockwright-capture/SKILL.md` — клип по участку, а не по зданию

**Acceptance Criteria:**
- [ ] `python tools/ge_convert.py --help` описывает `--clip-up` как верхнюю границу
- [ ] Попытка задать нижнюю границу по Y отвергается с объяснением
- [ ] `report.json` содержит проверку `the clip holds the site`
- [ ] Проверка красная, когда постройка выходит за границы эталона, и печатает коробку, которой хватило бы
- [ ] `python -m tools.pipeline_selftest` зелёный

**Verify:** `python -m tools.pipeline_selftest` → PASS; `python tools/ge_convert.py --help | grep -A2 clip-up` описывает только верх

**Steps:**

- [ ] **Step 1: Посмотреть, как `Clip` сейчас устроен**

```bash
sed -n '341,380p' tools/ge_convert.py
sed -n '715,750p' tools/ge_convert.py
```

- [ ] **Step 2: Запретить нижнюю границу по Y**

В `Clip` (или в разборе аргументов, где формируется `up`) оставить только верхний предел. Где сейчас пара, сделать одно число и добавить отказ:

```python
    # The floor is the datum and nothing else. A clip that starts above the
    # ground deletes everything low -- a four-metre clubhouse, a three-metre
    # terrace, a pool deck -- and deletes it silently: the reference simply has
    # no material there, so the skyline reads nothing, the section grades
    # nothing, and the comparison sheet shows a building standing on air. On the
    # site this rule was written after, the clip began 6.4 m up and took three
    # quarters of the plot with it.
    if lo is not None:
        raise SystemExit(
            "--clip-up takes a ceiling, not a floor. Clipping the bottom off a "
            "capture removes every low building on the site and removes it "
            "silently. If the terrain skirt is the problem, raise MESH_FLOOR in "
            "the building's probe -- that drops terrain from the measurement "
            "and keeps it in the file, so the loss is visible.")
```

- [ ] **Step 3: Написать строку гейта**

В `blockwright/grading.py`, рядом с `clip_box`:

```python
    def site_covered(self, g, derived, sched):
        """Whether the reference reaches as far as the build does.

        The clip is a decision made at stage one, when the only thing anybody
        has looked at is an ortho of the capture, and it is the decision that
        quietly bounded everything after it. Clipped around the building, the
        reference holds nothing of the grounds, so every part standing on them
        is graded by nothing and no row says so.

        This is that row. It compares the extent of what the build declared
        against the extent of the reference and prints the box that would have
        covered both, so that a re-clip is a copy of two numbers rather than a
        second look at an ortho.
        """
        mesh = derived.get("mesh", {}).get("bounds")
        if not mesh:
            g.ungraded("the clip holds the site",
                       "no reference: nothing states how far the site reaches")
            return
        built = sched.extent()          # (u0, u1, v0, v1) over every declaration
        over = [
            ("west", mesh["u0"] - built[0]),
            ("east", built[1] - mesh["u1"]),
            ("north", mesh["v0"] - built[2]),
            ("south", built[3] - mesh["v1"]),
        ]
        worst = [(side, gap) for side, gap in over if gap > 1.0]
        if not worst:
            g.add("the clip holds the site", True,
                  f"the build stands u {built[0]:.0f}..{built[1]:.0f}, "
                  f"v {built[2]:.0f}..{built[3]:.0f}, all of it inside the "
                  "reference")
            return
        g.add("the clip holds the site", False,
              "the build reaches past the reference by "
              + ", ".join(f"{gap:.0f} m {side}" for side, gap in worst)
              + f". Re-clip to hold u {min(built[0], mesh['u0']):.0f}.."
                f"{max(built[1], mesh['u1']):.0f}, "
                f"v {min(built[2], mesh['v0']):.0f}.."
                f"{max(built[3], mesh['v1']):.0f} and re-measure: everything "
                "out there is graded by nothing.")
```

- [ ] **Step 4: Добавить `Schedule.extent`**

В `blockwright/schedule.py`:

```python
    def extent(self) -> tuple[float, float, float, float]:
        """The bounding box of every declaration, in plan cells.

        What the build actually occupies, as opposed to what the plan drew: the
        two differ exactly where the build put something the plan never had, and
        that difference is what `site_covered` grades.
        """
        cells = [i for d in self.built.values()
                 for i, v in enumerate(d.mask.bits) if v]
        if not cells:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [i % self.width for i in cells]
        zs = [i // self.width for i in cells]
        return (float(min(xs)), float(max(xs)), float(min(zs)), float(max(zs)))
```

- [ ] **Step 5: Вызвать из `Grading.main`**

```python
        self.site_covered(g, derived, sched)
```

- [ ] **Step 6: Прогнать**

```bash
python -m tools.pipeline_selftest
python -m tools.coverage_selftest
```

Expected: оба PASS.

- [ ] **Step 7: Поправить скилл захвата**

В `.claude/skills/blockwright-capture/SKILL.md` заменить указания «снять клип-бокс вокруг здания» на «вокруг участка», и добавить абзац: нижняя граница по Y не режется никогда; если гейт сказал `the clip holds the site` красным — переклипать по напечатанным числам.

- [ ] **Step 8: Коммит**

```bash
git add tools/ge_convert.py blockwright/grading.py blockwright/schedule.py .claude/skills/blockwright-capture/SKILL.md
git commit -m "Refuse to clip the ground away, and say when the site outgrew the clip

Clipping the bottom off a capture removes every low building on the site and
removes it silently: the reference has no material there, so the skyline reads
nothing and the section grades nothing. One real clip began 6.4 m above the
datum and took a four-metre clubhouse and three-metre terraces with it, along
with three quarters of the plot horizontally.

The floor is now the datum and nothing else, and a new row compares what the
build occupies against what the reference covers -- printing the box that would
hold both, so a re-clip is a copy of two numbers rather than a second look at an
ortho."
```

---

### Task 8: Потолок объявляемых расхождений

**Goal:** `plan overlap` перестаёт быть объявляемым ключом `EXPECTED`; расхождение силуэтов ниже пола останавливает прогон, а `WITNESS = None` — законный способ сказать, что эталон свидетелем не является.

**Files:**
- Modify: `blockwright/survey.py` — `witnesses_of` отвергает объявление `plan overlap`; читает `WITNESS`
- Modify: `buildings/_template/probes/derive.py` — `WITNESS`, и комментарий у `EXPECTED` о том, чего в нём объявлять нельзя
- Modify: `docs/pipeline.md` — раздел про `EXPECTED`

**Acceptance Criteria:**
- [ ] `EXPECTED["plan overlap"]` вызывает `SystemExit` с объяснением
- [ ] Перекрытие силуэтов ниже пола без `WITNESS = None` останавливает `derive`
- [ ] `WITNESS = None` с причиной-фразой переводит все свидетельские строки в `[----]` с этой причиной
- [ ] `WITNESS = None` без причины (пустая строка) отвергается
- [ ] `python -m tools.pipeline_selftest` зелёный

**Verify:** `python -m tools.pipeline_selftest` → PASS

**Steps:**

- [ ] **Step 1: Добавить `WITNESS` в скелет**

```python
# Why the reference is not a witness to this building at all, as a sentence.
# None means it is, which is the normal case.
#
# The case this exists for: the plan is a crop of a game map and the capture is
# of the real prototype, and the two are drawings of different buildings. That
# is not a disagreement to declare in EXPECTED -- a declared disagreement is a
# fact about two views of one thing -- it is a decision about which of two
# buildings is being built, and it has to be made rather than excused.
#
# Set it, and every row the reference would have witnessed goes ungraded with
# this sentence printed beside it. Leave it, and a silhouette overlap under the
# floor stops the run.
WITNESS: str | None = None
```

- [ ] **Step 2: Отвергнуть `plan overlap` в `EXPECTED`**

В `Survey.witnesses_of`, в начале:

```python
        banned = set(self.t.EXPECTED) & {"plan overlap"}
        if banned:
            raise SystemExit(
                "EXPECTED cannot declare 'plan overlap'. Two silhouettes that "
                "share less than the floor are not one building seen twice, "
                "they are two buildings, and calling the difference expected "
                "hides the only row that says so.\n"
                "Decide instead: build what the drawn plan shows and set "
                "WITNESS to the sentence saying the reference is of something "
                "else, or build what the reference shows and let the plan "
                "answer only for the parts that matched.")
```

- [ ] **Step 3: Остановить прогон ниже пола**

Там, где `witnesses_of` считает перекрытие силуэтов:

```python
        if question == "plan overlap" and value < floor and not self.t.WITNESS:
            raise SystemExit(
                f"the drawn plan and the reference share {value:.2f} of their "
                f"silhouettes against a floor of {floor:.2f}. Two drawings of "
                "one building do not do that.\n"
                "Look at out/compare/top.png before anything else. If they are "
                "of different buildings -- a game map beside a capture of the "
                "real prototype is the usual way -- set WITNESS in this file to "
                "the sentence that says so, and every row the reference would "
                "have graded will read ungraded with that sentence. If they are "
                "the same building, the clip or the crop is wrong.")
```

- [ ] **Step 4: Провести `WITNESS` до строк**

Там, где формируется `out["witnesses"]`, при заданном `WITNESS` каждая запись получает `"ok": None` и `"expected": self.t.WITNESS` — существующий код `grading.witnesses` уже печатает такие как `[----]` с текстом.

Пустую строку отвергнуть:

```python
        if self.t.WITNESS is not None and not str(self.t.WITNESS).strip():
            raise SystemExit(
                "WITNESS is set to an empty string. It takes the reason the "
                "reference is not a witness, as a sentence -- the flag version "
                "of this is what let a real disagreement pass as expected.")
```

- [ ] **Step 5: Прогнать**

```bash
python -m tools.pipeline_selftest
```

Expected: PASS.

- [ ] **Step 6: Проверить оба отказа**

Во временной копии фикстуры добавить `EXPECTED = {"plan overlap": "..."}` — прогон обязан остановиться с текстом про два здания. Затем `WITNESS = ""` — остановиться с текстом про пустую строку. Убрать за собой.

- [ ] **Step 7: Обновить доки**

В `docs/pipeline.md` в разделе про `EXPECTED` добавить абзац: что объявлять можно (азимут, габарит, этаж — факты о входах) и что нельзя (перекрытие силуэтов — это не расхождение, а два разных объекта), и что `WITNESS` делает.

- [ ] **Step 8: Коммит**

```bash
git add blockwright/survey.py buildings/_template/probes/derive.py docs/pipeline.md
git commit -m "Stop letting a silhouette overlap of 0.51 be declared expected

EXPECTED is for disagreements that are facts about the inputs: a game map stands
its building on the game's street grid, a capture is of the real one, and their
bearings differ forever and correctly. A silhouette overlap under the floor is
not that. It says the two sources are of different buildings, and declaring it
expected turns the only row that could say so into a dash that reads exactly
like 'nothing could answer this'.

That is what happened on one real building: 0.51 against a floor of 0.70, three
rows greyed out, verdict ungraded, zero failures, and a build faithfully
reproducing a game map of somewhere else. The choice is now made rather than
excused -- WITNESS says the reference is of something else, in a sentence, and
greys out everything it would have graded."
```

---

### Task 9: Доки под новый механизм

**Goal:** `docs/pipeline.md`, `docs/sources.md`, `docs/data-contract.md`, команды и скиллы описывают участок, сборку по частям и происхождение — так, чтобы следующий человек читал метод, а не археологию.

**Files:**
- Modify: `docs/pipeline.md` — стадии 1-3 переписаны под участок и сборку по частям
- Modify: `docs/sources.md` — раздел о том, что авторитет источника применяется по частям
- Modify: `docs/data-contract.md` — `provenance`, `assembly`, `CAPTURE_PARTS`, `UNMEASURED`, `WITNESS`
- Modify: `.claude/skills/blockwright/SKILL.md`, `.claude/skills/blockwright-measure/SKILL.md`, `.claude/commands/blockwright-run.md`, `.claude/commands/blockwright-new.md`

**Acceptance Criteria:**
- [ ] `docs/pipeline.md` описывает клип по участку и запрет резать снизу
- [ ] `docs/pipeline.md` описывает сборку плана по частям и говорит, что одноветочной выборки больше нет
- [ ] `docs/data-contract.md` перечисляет все новые ключи `derived.json` и все новые таблицы скелета
- [ ] `.claude/commands/blockwright-run.md` в шаге обмера велит читать таблицу покрытия первой
- [ ] `grep -rn "одну ветку\|одноветочн" docs/` не находит утверждений, что выбирается одна ветка

**Verify:** `python -m tools.pipeline_selftest` → PASS (доки код не ломают, но прогон подтверждает, что ничего не задето)

**Steps:**

- [ ] **Step 1: Переписать стадию 1 в `docs/pipeline.md`**

Заменить абзацы про клип-бокс: клип теперь по участку. Добавить числа из разбора 009 — 190×181 против 86×97 м, нижняя граница 6.4 м, три четверти площадки, цена 1.8× — как обоснование, ровно в стиле остальных разделов документа.

- [ ] **Step 2: Переписать стадию 2**

Раздел «Четыре ветки, одна развилка» становится «Развилка решает, кто рисует план; остальное добирается с эталона». Добавить подраздел о сопоставлении по IoU и о том, почему порог 0.30.

- [ ] **Step 3: Дописать стадию 3**

Добавить абзац про `provenance` и таблицу покрытия: три слова на часть, и почему их не выводили раньше (всё было выводимо из других ключей, и никто не выводил).

- [ ] **Step 4: Обновить `docs/sources.md`**

Добавить раздел: таблица авторитетности применяется **по частям**, а не один раз на здание. Карта с двойкой за план имеет двойку за те части, которые нарисовала, и ноль за те, которых на ней нет.

- [ ] **Step 5: Обновить `docs/data-contract.md`**

Перечислить: `derived.json["parts"][].provenance`, `derived.json["assembly"]`, и таблицы `CAPTURE_PARTS`, `MATCH_FLOOR`, `PART_LEAST_AREA`, `PART_LEAST_HEIGHT`, `WITNESS` в `probes/derive.py`, `UNMEASURED` в `gate.py`.

- [ ] **Step 6: Обновить команду прогона**

В `.claude/commands/blockwright-run.md`, шаг 2 (обмер): первое, что читается после запуска, — **таблица покрытия**, и часть с тремя прочерками решается до того, как что-либо строится.

- [ ] **Step 7: Прогнать всё**

```bash
python -m tools.pipeline_selftest && python -m tools.coverage_selftest && python -m tools.lint_buildings
```

Expected: три PASS.

- [ ] **Step 8: Коммит**

```bash
git add docs/ .claude/
git commit -m "Document the site as the unit and the plan as an assembly

The method document described four branches and one winner, which is what the
code did and is no longer what it does. It also described a clip cut around the
building as the right answer, with a note that the grounds are lost by
construction and judged by comparison sheets instead -- and that note turned out
to be describing a hole rather than a trade."
```

---

## Self-Review

**Spec coverage:**

| Раздел спеки | Задача |
|---|---|
| 2.1 Клип по участку | 7 |
| 2.2 План собирается по частям | 5, 6 |
| 2.3 Происхождение у каждой части | 3, 4 |
| 2.4 Расхождение источников не объявляется | 8 |
| 5 Что выпиливается (стадия 6) | 1 |
| 7.1 Заморозка — удаление одноветочной выборки | 5 |
| Доки | 9 |

Разделы 3 (этапы и ворота), 4 (ручной режим) и 3.1-3.3 (грейбокс) покрываются вторым планом: [2026-08-17-greybox-journal-manual-mode.md](2026-08-17-greybox-journal-manual-mode.md).

**Placeholder scan:** проверено — все шаги содержат либо код, либо конкретную команду с ожидаемым выводом. Шаги 1-6 задачи 9 описывают правку прозы и потому не несут кода; они называют, какой раздел какого файла и что именно в нём меняется.

**Type consistency:** `Read.provenance` — `dict[str, str]`, заводится в задаче 3 и читается в задачах 4 и 5. `Survey.overlap` — статический, возвращает `float`. `Schedule.extent()` — `tuple[float, float, float, float]`, заводится в задаче 7. Имена таблиц скелета (`CAPTURE_PARTS`, `MATCH_FLOOR`, `PART_LEAST_AREA`, `PART_LEAST_HEIGHT`, `WITNESS`, `UNMEASURED`) употребляются одинаково во всех задачах.
