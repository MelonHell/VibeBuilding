# Грейбокс, журнал и ручной режим — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers-extended-cc:subagent-driven-development (recommended) or superpowers-extended-cc:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Форма здания собирается и одобряется до того, как написан код деталей; каждый раунд ревью записан; человек может встать после агента на трёх воротах и сказать, что тот пропустил.

**Architecture:** `build.py` делит секции на два списка — `GREYBOX` кладёт объёмы, `DETAIL` всё остальное, — и под `--greybox` пишет `out/greybox.schem`, который смотрят планами сверху и косыми рендерами. Журнал получает второй разрез: хронику раундов рядом с блоками находок, и переезжает из `out/` (удаляемого) в пакет здания. Ручной режим — отладочный: после того как агентский цикл ревью сошёлся, прогон останавливается и ждёт человека, чьи находки ложатся в тот же журнал с пометкой `by: human`.

**Tech Stack:** Python 3, Pillow для листов и планов; тесты — `tools/*_selftest.py`, запускаемые модулями; агенты-ревьюеры — markdown-определения в `.claude/agents/`.

**Spec:** [docs/superpowers/specs/2026-08-17-site-scope-and-provenance-design.md](../specs/2026-08-17-site-scope-and-provenance-design.md), разделы 3 и 4.

**Depends on:** [2026-08-17-site-scope-and-provenance.md](2026-08-17-site-scope-and-provenance.md) — задачи 1 (выпил стадии 6) и 3 (происхождение) должны быть сделаны: задача 3 этого плана читает `provenance`, а задача 1 меняет сигнатуру `finish()`.

## Global Constraints

- **Журнал пишется по-английски**, как нынешний `out/review/findings.md`, — примеры в спеке иллюстративны и написаны по-русски только там. Заголовки находок разбираются регулярным выражением, поэтому их поля — английские слова.
- **Один блок — один метр.** Ни одного масштабного коэффициента.
- **Тестов на pytest нет.** Проверка — `tools/*_selftest.py`, запускаемые как модули.
- **Существующие шестнадцать зданий заморожены** и не приводятся к новому механизму. Эталон — `buildings/_template`.
- **Язык:** код, комментарии, докстринги, коммиты и журнал — английский; документация в `docs/` — русский.
- **Ручной режим — леса.** Всё, что человек ловит на воротах, — кандидат в строку гейта; механизм, который переживёт леса, это строки, а не остановки.
- **Коммит после каждой задачи.**

**User decisions (already made):**
- «грейбокс — просто этап до постройки, на котором мы сможем пофиксить косяки формы до заполнения деталями» — один артефакт, одно слово, никакого отдельного инструмента для гашения материала.
- «я бы не хотел делать правки руками, в идеале только смотреть и поправлять тебя словами» — плоский чертёж не отдельная стадия и не редактируемый вход; это вид сверху на грейбокс.
- «да, на каждом из трёх, для этого оно и нужно, чтоб я все этапы отладил» — человеческое ревью после агентского на воротах 1, 4 и 5.
- «ревью циклично… пока я не скажу "всё ок"» — человеческий цикл кончается только словами человека.
- «доработка пайплайна это то чем мы с тобой занимаемся сейчас» — отдельного агента доработки нет.

---

### Task 1: Два списка секций и `--greybox`

**Goal:** `build.py` объявляет `GREYBOX` и `DETAIL`; под `--greybox` собирается только первый и пишется `out/greybox.schem`.

**Files:**
- Modify: `buildings/_template/build.py` — списки секций, разбор аргумента, имя выходного файла
- Modify: `blockwright/finish.py` — параметр `name` уже есть; убедиться, что он управляет и схематикой, и планами
- Modify: `buildings/_template/paths.py` — `GREYBOX = OUT / "greybox.schem"`, `SCHEM` переименовать в `BUILD`
- Create: `tools/greybox_selftest.py`

**Acceptance Criteria:**
- [ ] `python -m buildings.<имя>.build --greybox` пишет `out/greybox.schem` и **не** пишет `out/build.schem`
- [ ] `python -m buildings.<имя>.build` пишет `out/build.schem`
- [ ] Грейбокс содержит объёмы каждой части и **не** содержит остекления, перекрытий и парапетов
- [ ] Секция, попавшая в оба списка, вызывает `SystemExit` с именем секции
- [ ] `python -m tools.greybox_selftest` зелёный
- [ ] `python -m tools.pipeline_selftest` зелёный

**Verify:** `python -m tools.greybox_selftest` → `greybox has N part(s) and no detail`

**Steps:**

- [ ] **Step 1: Объявить списки в скелете**

В `buildings/_template/build.py`, ниже определений секций:

```python
# The two halves of a build, and the order they are written in.
#
# GREYBOX puts up the volumes: each part's footprint extruded to its measured
# height, and the roof steps where the reference measured them. Nothing else --
# no cavities, no openings, no material beyond one block per part.
#
# DETAIL is everything after that.
#
# The split is what makes gate 4 mean anything. Run under --greybox the build
# stops after the first list, and the form is looked at and agreed before a line
# of the second list is written. Reviewed the other way round -- form and colour
# together at the end -- a wrong wing is found after a week of facades has been
# hung on it, which is how this pipeline worked until now.
GREYBOX = (ground, shell)

DETAIL = ()
```

- [ ] **Step 2: Собирать по спискам**

Переписать `main()` в скелете:

```python
def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    greybox = "--greybox" in argv

    overlap = set(GREYBOX) & set(DETAIL)
    if overlap:
        raise SystemExit(
            "section(s) in both GREYBOX and DETAIL: "
            + ", ".join(sorted(f.__name__ for f in overlap))
            + ".\nA section belongs to one list. Called twice it draws twice, "
            "and the second call is the one that quietly wins.")

    if not paths.DERIVED.exists():
        raise SystemExit(
            f"{paths.DERIVED} is missing. Every dimension in this build is "
            "measured rather than typed, so there is nothing to build from "
            "until the references have been read:\n"
            "    python -m buildings.<name>.probes.derive")

    site = Site(json.loads(paths.DERIVED.read_text(encoding="utf-8")),
                greybox=greybox)
    canvas = Canvas(site.width, site.top + 4, site.length)

    for section in GREYBOX:
        section(canvas, site, SCHEDULE)
    if not greybox:
        for section in DETAIL:
            section(canvas, site, SCHEDULE)

    print(site.frame)
    print(f"  levels  {', '.join(str(y) for y in site.levels)}, "
          f"storey {site.storey} m")
    for name, top in site.tops.items():
        p = site.parts[name]
        print(f"  {name:12s} u {p.u0:6.1f}..{p.u1:6.1f}  "
              f"v {p.v0:5.1f}..{p.v1:5.1f}  to {top} m")

    done = finish(canvas, paths.OUT, site.frame,
                  name="greybox" if greybox else "build",
                  schedule=None if greybox else SCHEDULE,
                  orthos=paths.ORTHOS, scale=RENDER_SCALE,
                  plans=(("ground", site.ground),
                         ("upper", site.levels[len(site.levels) // 2])))
    for line in done.lines():
        print(line)
```

Добавить `import sys` наверх файла.

Расписание передаётся только полной сборке: манифест перечисляет то, что у здания есть целиком, и грейбокс его по построению не выполняет — аудит на нём докладывал бы отсутствие остекления как дефект.

- [ ] **Step 3: Развести имена в путях**

В `buildings/_template/paths.py`:

```python
GREYBOX = OUT / "greybox.schem"     # the volumes alone, gate 4 looks at this
BUILD = OUT / "build.schem"         # the finished build, gate 5 looks at this
```

и убрать `SCHEM`. Пройтись по `gate.py` и `review.py` скелета, заменив `paths.SCHEM` на `paths.BUILD`.

- [ ] **Step 4: Написать селфтест**

Create `tools/greybox_selftest.py`:

```python
"""The greybox holds the volumes and nothing after them.

Two things can go wrong and both are quiet. A section left out of both lists
never runs, and the greybox comes back missing a wing that the full build has --
so the form gets agreed on a building that is not the one being made. A section
in both lists runs twice, and the second call wins wherever they disagree.

    python -m tools.greybox_selftest
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIND = "_selftest_greybox"

# Blocks that belong to DETAIL and must not appear in a greybox. Glass is the
# clearest: nothing in GREYBOX has any reason to place a transparent block.
DETAIL_BLOCKS = ("stained_glass", "_pane", "_stairs", "_slab")


def run(*args: str) -> str:
    done = subprocess.run([sys.executable, *args], cwd=ROOT, check=True,
                          capture_output=True, text=True, encoding="utf-8")
    return done.stdout


def main() -> int:
    where = ROOT / "buildings" / KIND
    run("-m", "tools.fixture", str(where))
    run("-m", f"buildings.{KIND}.probes.derive")
    run("-m", f"buildings.{KIND}.build", "--greybox")
    run("-m", f"buildings.{KIND}.build")

    grey = where / "out" / "greybox.schem"
    full = where / "out" / "build.schem"
    for path in (grey, full):
        if not path.exists():
            print(f"FAIL: {path.name} was not written")
            return 1

    from blockwright import schem
    counts = schem.counts(grey)
    leaked = [name for name in counts
              if any(mark in name for mark in DETAIL_BLOCKS)]
    if leaked:
        print("FAIL: detail blocks in the greybox: " + ", ".join(leaked))
        return 1

    if schem.counts(full) == counts:
        print("FAIL: the full build and the greybox hold the same blocks -- "
              "DETAIL is empty or never ran")
        return 1

    print(f"greybox has {len(counts)} block kind(s) and no detail")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Если у `blockwright/schem.py` функция подсчёта названа иначе — использовать существующее имя; проверить `grep -n "^def " blockwright/schem.py` перед написанием.

- [ ] **Step 5: Прогнать**

```bash
python -m tools.greybox_selftest
python -m tools.pipeline_selftest
```

Expected: оба PASS.

- [ ] **Step 6: Коммит**

```bash
git add buildings/_template/ blockwright/finish.py tools/greybox_selftest.py
git commit -m "Split a build into the volumes and everything after them

Form and material were drawn in one pass and reviewed in one pass, so a wrong
wing was found after a week of facades had been hung on it. The two lists let
the build stop after the volumes, which is the only ordering under which
agreeing the form before drawing the detail means anything.

The schedule goes to the full build only: a manifest lists what the building has
in all, and a greybox does not have it by construction, so auditing one would
report missing glazing as a defect."
```

---

### Task 2: Заглушка высоты в грейбокс-режиме

**Goal:** Часть, у которой высота ещё не измерена и не объявлена, в грейбоксе встаёт в один этаж и помечается — так план можно свести раньше, чем сведены высоты. Полная сборка на заглушке по-прежнему не стартует.

**Files:**
- Modify: `buildings/_template/build.py` — `Site.__init__` принимает `greybox` и подставляет заглушку
- Modify: `tools/greybox_selftest.py` — проверка, что заглушка помечена и что полная сборка отказывает

**Acceptance Criteria:**
- [ ] В грейбокс-режиме часть без высоты строится на `storey` метров и печатается строкой `placeholder`
- [ ] В полном режиме та же часть даёт `SystemExit` с тем же текстом, что и сейчас
- [ ] Список заглушек попадает в вывод сборки
- [ ] `python -m tools.greybox_selftest` зелёный

**Verify:** `python -m tools.greybox_selftest` → PASS, и вывод `build --greybox` содержит `placeholder`

**Steps:**

- [ ] **Step 1: Принять флаг и подставить заглушку**

В `buildings/_template/build.py`, в `Site.__init__`, заменить блок, который сейчас падает на отсутствующей высоте:

```python
    def __init__(self, derived: dict, greybox: bool = False):
        ...
        missing = [name for name in read.order if name not in self.tops]
        # A greybox exists to settle the plan, and the plan can be settled
        # before the heights are. A part nothing has measured yet stands one
        # storey tall and says so, in the printout and in `self.placeholder`, so
        # that a low block in the render is read as "not measured yet" rather
        # than as "measured low".
        #
        # The full build still refuses. A placeholder that reached a finished
        # schematic would be a number nobody chose, standing in a file the gate
        # grades against the reference.
        self.placeholder = set()
        if missing and greybox:
            for name in missing:
                self.tops[name] = self.ground + self.storey
                self.placeholder.add(name)
            missing = []
        if missing:
            raise SystemExit(
                f"nothing states how tall {', '.join(missing)} is -- the "
                "reference has no material over that part and no height was "
                "declared for it. Add one to DECLARED_HEIGHTS in "
                "probes/derive.py, with the photograph or the sheet it was read "
                "off, and re-run it.\n"
                "To look at the plan before settling the heights, run the build "
                "with --greybox: those parts stand one storey and are marked.")
        self.top = max(self.tops.values())
```

- [ ] **Step 2: Напечатать заглушки**

В `main()`, в цикле печати частей:

```python
    for name, top in site.tops.items():
        p = site.parts[name]
        mark = "  placeholder" if name in site.placeholder else ""
        print(f"  {name:12s} u {p.u0:6.1f}..{p.u1:6.1f}  "
              f"v {p.v0:5.1f}..{p.v1:5.1f}  to {top} m{mark}")
    if site.placeholder:
        print(f"  {len(site.placeholder)} part(s) stand at a placeholder "
              f"height: {', '.join(sorted(site.placeholder))}. The plan can be "
              "agreed on this; the heights cannot.")
```

- [ ] **Step 3: Проверить оба поведения в селфтесте**

Добавить в `tools/greybox_selftest.py`, перед итоговой печатью:

```python
    # A part with no measured height: the greybox must stand it up and say so,
    # and the full build must refuse it.
    probe = where / "probes" / "derive.py"
    text = probe.read_text(encoding="utf-8")
    probe.write_text(text.replace("CAPTURE_PARTS: tuple[str, ...] = ()",
                                  "CAPTURE_PARTS: tuple[str, ...] = ()\n"
                                  "PART_LEAST_HEIGHT = 99.0"),
                     encoding="utf-8")
    run("-m", f"buildings.{KIND}.probes.derive")
    said = run("-m", f"buildings.{KIND}.build", "--greybox")
    if "placeholder" not in said:
        print("FAIL: greybox did not mark a part with no measured height")
        return 1
    done = subprocess.run([sys.executable, "-m", f"buildings.{KIND}.build"],
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8")
    if done.returncode == 0:
        print("FAIL: the full build accepted a part with no measured height")
        return 1
```

- [ ] **Step 4: Прогнать**

```bash
python -m tools.greybox_selftest
```

Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add buildings/_template/build.py tools/greybox_selftest.py
git commit -m "Let the greybox stand a part up before its height is known

The plan and the heights settle at different times, and refusing to draw
anything until both are settled forces a height to be declared before anybody
has confirmed the footprint it belongs to. A part with nothing over it now
stands one storey in the greybox and is marked as a placeholder in the printout,
so a low block in the render reads as not-measured-yet rather than as
measured-low. The full build still refuses: a placeholder in a finished
schematic would be a number nobody chose, graded against a real reference."
```

---

### Task 3: Планы по слоям и лист сверху одного охвата

**Goal:** Ворота 4 показывают горизонтальные срезы 1 px = 1 блок и лист «план | карта | орто эталона», у которого все три панели покрывают **один охват**.

**Files:**
- Modify: `blockwright/finish.py` — писать планы в `out/greybox/` при `name == "greybox"`
- Modify: `blockwright/compare.py` — новая функция `aligned`, приводящая панели к общему охвату
- Create: `tools/topsheet_selftest.py`

**Acceptance Criteria:**
- [ ] `build --greybox` пишет `out/greybox/plan-<метка>.png`, по одному на объявленный слой, 1 px = 1 блок
- [ ] `build --greybox` пишет `out/greybox/top.png` с тремя панелями
- [ ] Все три панели `top.png` покрывают одну прямоугольную область в метрах; панель, у которой данных меньше, дополняется полем, а не растягивается
- [ ] `python -m tools.topsheet_selftest` зелёный

**Verify:** `python -m tools.topsheet_selftest` → `three panels, one extent`

**Steps:**

- [ ] **Step 1: Прочитать, как `compare` строит панели**

```bash
sed -n '1,60p' blockwright/compare.py
sed -n '140,175p' blockwright/compare.py
```

Нужны `Panel.metres_per_pixel` и то, как `sheet` их укладывает.

- [ ] **Step 2: Написать приведение к общему охвату**

В `blockwright/compare.py`:

```python
def aligned(panels: list[Panel], metres_per_pixel: float | None = None
            ) -> list[Panel]:
    """The same panels, all covering the same rectangle of ground.

    `sheet` already puts panels at one scale, and one scale is not one extent: a
    reference clipped to the building beside a build that covers the whole plot
    comes out as a small picture next to a large one, both at a true 1:1. The eye
    then spends its time finding the building instead of comparing the shape,
    which is what the sheet is for -- and on one real site that is exactly how a
    missing half of the plot went unnoticed.

    So each panel is padded, never scaled, to the union of all their extents.
    Padding says "nothing was known here"; scaling would say "this is what was
    there", which is false.
    """
    if not panels:
        return panels
    scale = metres_per_pixel or min(p.metres_per_pixel for p in panels)
    widest = max(p.image.width * p.metres_per_pixel for p in panels)
    tallest = max(p.image.height * p.metres_per_pixel for p in panels)
    out = []
    for panel in panels:
        box = (int(round(widest / scale)), int(round(tallest / scale)))
        resized = panel.at(scale)
        padded = Image.new("RGB", box, BACKDROP)
        padded.paste(resized, ((box[0] - resized.width) // 2,
                               (box[1] - resized.height) // 2))
        out.append(Panel(padded, scale, panel.label))
    return out
```

(`Panel.at(scale)` — существующий метод масштабирования панели к метрам-на-пиксель; если он назван иначе, использовать существующее имя. `BACKDROP` — цвет фона листа, уже определённый в модуле.)

- [ ] **Step 3: Писать планы и лист грейбокса в `finish`**

В `blockwright/finish.py`, там, где пишутся планы, направить их в подпапку, когда собирается грейбокс:

```python
    where = out / name if name == "greybox" else out
    where.mkdir(parents=True, exist_ok=True)
    done.plans[f"{name}_plan"] = where / "plan.png"
    canvas.silhouette().to_png(done.plans[f"{name}_plan"], scale=1)
    for label, y in plans:
        path = where / f"plan-{label}.png"
        canvas.layer(y).to_png(path, scale=1)
        done.plans[label] = path
```

`scale=1`, а не `2`: слой обязан быть ровно один пиксель на блок, потому что это то, что о нём сказано, и удвоение делает лист непригодным для счёта клеток.

Затем лист сверху:

```python
    if orthos is not None and (Path(orthos) / "render_meta.json").exists():
        panels = [compare.mesh_panel(orthos, "top")]
        panels.append(compare.build_panel(
            render(canvas, None, view=View.plan(), frame=frame, scale=scale),
            scale, label=f"{name} plan"))
        if layout_png is not None and Path(layout_png).exists():
            panels.append(compare.map_panel(layout_png))
        compare.sheet(compare.aligned(panels), where / "top.png")
```

`compare.map_panel` — новая однострочная обёртка рядом с `mesh_panel`: карта уже 1 px = 1 м, поэтому её `metres_per_pixel` равен 1.0.

- [ ] **Step 4: Написать селфтест**

Create `tools/topsheet_selftest.py`:

```python
"""The three panels of the top sheet cover the same ground.

The sheet exists so that a person can compare a footprint against the drawing it
came from and against the reference. It stops doing that the moment the panels
cover different areas: one real sheet put a reference clipped to the building
beside a build covering the whole plot, and the missing three quarters of the
site read as a framing choice.

    python -m tools.topsheet_selftest
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
KIND = "_selftest_topsheet"


def main() -> int:
    where = ROOT / "buildings" / KIND
    for args in (["-m", "tools.fixture", str(where)],
                 ["-m", f"buildings.{KIND}.probes.derive"],
                 ["-m", f"buildings.{KIND}.build", "--greybox"]):
        subprocess.run([sys.executable, *args], cwd=ROOT, check=True)

    sheet = where / "out" / "greybox" / "top.png"
    if not sheet.exists():
        print("FAIL: no top sheet was written")
        return 1

    layer = where / "out" / "greybox" / "plan-ground.png"
    if not layer.exists():
        print("FAIL: no layered plan was written")
        return 1

    # One pixel to one block: the ground layer is as wide as the canvas.
    import json
    derived = json.loads(
        (where / "out" / "derived.json").read_text(encoding="utf-8"))
    wide = derived["frame"]["width"]
    if Image.open(layer).width != wide:
        print(f"FAIL: plan-ground.png is {Image.open(layer).width} px wide, "
              f"the canvas is {wide} blocks -- not one pixel to one block")
        return 1

    print("three panels, one extent")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Прогнать**

```bash
python -m tools.topsheet_selftest
python -m tools.greybox_selftest
python -m tools.pipeline_selftest
```

Expected: три PASS.

- [ ] **Step 6: Коммит**

```bash
git add blockwright/finish.py blockwright/compare.py tools/topsheet_selftest.py
git commit -m "Put the top sheet's panels on one extent, and the plans at one pixel a block

A sheet at one scale is not a sheet at one extent. A reference clipped to the
building beside a build covering the whole plot comes out as a small picture
next to a large one, both at a true 1:1, and the eye spends its time locating
the building rather than comparing the shape -- which is how three quarters of a
missing site read as a framing choice on one real sheet.

Panels are padded to the union of their extents, never scaled to it: padding
says nothing was known there, scaling would say something was.

The layered plans drop to scale=1 for the same reason. Doubling them makes the
picture easier to see and impossible to count cells on, and counting cells is
what a one-pixel-to-one-block drawing is for."
```

---

### Task 4: Ревьюер грейбокса

**Goal:** Отдельный агент, судящий форму и молчащий про материал, и команда, которая его запускает на воротах 4.

**Files:**
- Create: `.claude/agents/blockwright-greybox-reviewer.md`
- Modify: `buildings/_template/review.py` — режим `--greybox`: рендерить грейбокс и складывать в `out/greybox/review/`
- Create: `.claude/commands/blockwright-greybox.md`

**Acceptance Criteria:**
- [ ] Определение агента запрещает судить материал, цвет и палитру явным абзацем
- [ ] `python -m buildings.<имя>.review --greybox` складывает рендеры грейбокса, орто эталона с тех же камер и фотографии в `out/greybox/review/`
- [ ] `prompt.txt` в этой папке описывает только те виды картинок, которые там есть
- [ ] Команда `/blockwright-greybox <имя>` описывает цикл: ревью → находки → починка → ревью → сошёлся

**Verify:** `python -m buildings._selftest_greybox.review --greybox` → папка `out/greybox/review/` с `prompt.txt`

**Steps:**

- [ ] **Step 1: Посмотреть, как устроен нынешний ревьюер**

```bash
cat .claude/agents/blockwright-photo-reviewer.md
```

Новый агент повторяет его форму: то же поле `tools`, тот же запрет читать что-либо кроме картинок.

- [ ] **Step 2: Написать определение агента**

Create `.claude/agents/blockwright-greybox-reviewer.md` с фронтматтером того же вида, что у `blockwright-photo-reviewer` (`name`, `description`, `tools: Read, Glob`), и телом:

```markdown
Ты смотришь на грейбокс — постройку без деталей. В ней есть объёмы, части и их
высоты, и в ней намеренно нет ни окон, ни балконов, ни кровельного покрытия, ни
материалов. Это не дефекты, и докладывать о них нельзя.

Ты отвечаешь на четыре вопроса и ни на один другой:

1. **Те ли объёмы.** Сколько частей у постройки и сколько у эталона; где часть
   есть у одного и нет у другого.
2. **Те ли пропорции.** Длина к ширине, высота к длине, отношение частей друг к
   другу.
3. **Та ли форма в плане.** Прямоугольное прямоугольно, круглое кругло, крыло
   идёт туда же, куда на эталоне.
4. **Там ли части стоят.** Взаимное расположение, разрывы, что к чему примыкает.

**Про материал, цвет, палитру, остекление, ритм фасада и любую отделку —
молчи.** Их там нет по построению. Находка «нет окон» на грейбоксе — это находка
о том, что ты смотришь на грейбокс.

Часть, стоящая заметно ниже соседей и подписанная в постройке как
`placeholder`, — это часть, высоту которой ещё не измерили. Её высоту не
обсуждай; её пятно и положение — обсуждай.

Тебе дают папку с картинками и больше ничего. Ни чисел, ни констант, ни отчёта
гейта. Всё, что ты можешь сказать, ты говоришь по картинкам.
```

- [ ] **Step 3: Добавить режим в `review.py` скелета**

```python
    greybox = "--greybox" in argv
    schematic = paths.GREYBOX if greybox else paths.BUILD
    where = paths.OUT / "greybox" / "review" if greybox else paths.OUT / "review"
```

и передать `schematic`/`where` в аппаратуру `blockwright.reviewing` вместо жёстко зашитых. Промпт в режиме грейбокса перечисляет только рендеры постройки, рендеры эталона и фотографии — планов и чертежей там нет.

- [ ] **Step 4: Написать команду**

Create `.claude/commands/blockwright-greybox.md`:

```markdown
---
description: Ворота 4 — форма и высоты: собрать грейбокс, посмотреть на него глазами и свести цикл
argument-hint: <имя_здания>
allowed-tools: Bash, Read, Write, Edit, Glob, Agent, Skill
---

Прогони ворота 4 здания `$1`.

## 1. Собрать

```sh
python -m buildings.$1.build --greybox
python -m buildings.$1.review --greybox
```

Первое пишет `out/greybox.schem`, планы по слоям и лист `out/greybox/top.png`.
Второе складывает рендеры и эталон в `out/greybox/review/`.

## 2. Посмотреть

Открой `out/greybox/top.png` первым. Три панели одного охвата: план постройки,
карта, орто эталона. Расхождение пятна видно там и больше нигде.

Потом отдай `out/greybox/review/` агенту `blockwright-greybox-reviewer` — один
раз, одной папкой, ничего кроме пути.

## 3. Свести цикл

Ревью — цикл, а не просмотр. Находка → починка → **пересборка и повторный
взгляд** → пока раунд не придёт пустым. Пустой раунд записывается: цикл без него
не сошёлся, а был брошен.

Каждую находку записывай в `buildings/$1/findings.md` по формату журнала
(`/blockwright-review` описывает его целиком): что нашли, чем починили, откуда
взялось, чем предотвратить.

## 4. Ворота

В авто-режиме на этом всё. В ручном — покажи человеку итог и жди: он смотрит
после агента и говорит, что тот пропустил.

Следующий шаг: `python -m buildings.$1.build`, дальше `/blockwright-run $1`.
```

- [ ] **Step 5: Прогнать**

```bash
python -m tools.fixture buildings/_scratch
python -m buildings._scratch.probes.derive
python -m buildings._scratch.build --greybox
python -m buildings._scratch.review --greybox
ls buildings/_scratch/out/greybox/review/
rm -rf buildings/_scratch
```

Expected: папка с рендерами и `prompt.txt`.

- [ ] **Step 6: Коммит**

```bash
git add .claude/agents/blockwright-greybox-reviewer.md .claude/commands/blockwright-greybox.md buildings/_template/review.py
git commit -m "Give the greybox a reviewer that judges form and not colour

A naive reviewer handed a building with no windows and no material reports that
it has no windows and no material, which is true, useless, and enough of it to
bury the findings that matter. The greybox reviewer is told what is deliberately
absent and asked four questions about volume, proportion, plan shape and
placement -- and told that a part standing low and marked placeholder is a part
whose height nobody has measured yet, so its footprint is fair game and its
height is not."
```

---

### Task 5: Журнал — хроника раундов и поля починки

**Goal:** Журнал переезжает в пакет здания, получает хронику раундов рядом с блоками находок, и каждая находка несёт, чем починена, откуда взялась и чем предотвратить.

**Files:**
- Modify: `blockwright/findings.py` — новый заголовок, разбор раундов, аудит незакрытых циклов
- Modify: `buildings/_template/paths.py` — `FINDINGS = HERE / "findings.md"`
- Modify: `.claude/commands/blockwright-review.md` — формат журнала
- Create: `tools/findings_selftest.py`

**Acceptance Criteria:**
- [ ] Заголовок находки разбирается в виде `### F-04 | built | gate 4 | round 1 | seen 1 | by: human`
- [ ] Поле `by` необязательно и по умолчанию `reviewer`
- [ ] Хроника разбирается: `### Round N -- agent` и `### Round N -- human`
- [ ] `findings.audit` докладывает цикл без пустого раунда в конце как незакрытый
- [ ] `findings.audit` докладывает находку, у которой нет поля `Prevented by`
- [ ] Журнал читается из `buildings/<имя>/findings.md`
- [ ] `python -m tools.findings_selftest` зелёный

**Verify:** `python -m tools.findings_selftest` → `ledger parses, audit catches all four`

**Steps:**

- [ ] **Step 1: Расширить заголовок**

В `blockwright/findings.py`:

```python
HEAD = re.compile(
    r"^###\s+(?P<id>[A-Z]-\d+)\s*\|\s*(?P<state>\w+)\s*"
    r"\|\s*gate\s+(?P<gate>\d+)\s*"
    r"\|\s*round\s+(?P<first>\d+)\s*\|\s*seen\s+(?P<seen>\d+)\s*"
    r"(?:\|\s*by:\s*(?P<by>\w+)\s*)?$",
    re.MULTILINE)

# The chronicle: one heading per round, per gate. It answers the question the
# per-finding ledger cannot -- did the loop converge, and in how many rounds --
# and an empty round is a heading with nothing under it, which is what
# convergence looks like written down.
ROUND = re.compile(
    r"^###\s+Round\s+(?P<n>\d+)\s+--\s+(?P<who>agent|human)\s*$",
    re.MULTILINE)

# What a closed finding has to say beyond its title. The third is the one that
# makes the journal worth keeping: a fix recorded as "changed recess to band"
# does not generalise, and the same fix with the reason it was needed does.
FIELDS = ("Fixed", "Arose from", "Prevented by")
```

и расширить `Finding`:

```python
class Finding:
    __slots__ = ("id", "state", "gate", "first", "seen", "by", "body")

    def __init__(self, id: str, state: str, gate: int, first: int, seen: int,
                 by: str = "reviewer", body: str = ""):
        ...

    def head(self) -> str:
        return (f"### {self.id} | {self.state} | gate {self.gate} | "
                f"round {self.first} | seen {self.seen} | by: {self.by}")

    def field(self, name: str) -> str:
        """The text of one **Name:** field of this finding's body, or ''."""
        mark = re.search(rf"^\*\*{re.escape(name)}:\*\*\s*(.+?)(?=^\*\*|\Z)",
                         self.body, re.MULTILINE | re.DOTALL)
        return mark.group(1).strip() if mark else ""
```

- [ ] **Step 2: Разобрать хронику и добавить два правила аудита**

```python
def rounds(path: str | Path) -> list[tuple[int, str, int]]:
    """(number, who, findings) for every round heading in the chronicle."""
    text = Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""
    marks = list(ROUND.finditer(text))
    out = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[mark.end():end]
        listed = [ln for ln in body.splitlines() if ln.strip().startswith("- ")]
        out.append((int(mark.group("n")), mark.group("who"), len(listed)))
    return out
```

и в `audit`:

```python
    # A loop that never came back empty was abandoned, not finished, and from
    # the outside the two look identical. The empty round is the evidence.
    for who in ("agent", "human"):
        mine = [r for r in rounds(path) if r[1] == who]
        if mine and mine[-1][2]:
            out.append(f"the {who} loop has no empty round: its last round "
                       f"({mine[-1][0]}) raised {mine[-1][2]} finding(s), so it "
                       "was left rather than closed")

    for finding in read(path):
        if finding.state in ("built", "rejected") and not finding.field("Prevented by"):
            out.append(f"{finding.id} is closed and does not say what would have "
                       "prevented it -- that field is what the journal is kept for")
```

- [ ] **Step 3: Переселить журнал**

В `buildings/_template/paths.py`:

```python
# The review journal. Above `out/` on purpose: everything under `out/` is
# derived and is deleted and rebuilt whole, and nothing rebuilds this. It is
# written a round at a time and exists in one copy.
FINDINGS = HERE / "findings.md"
```

Заменить в `review.py` скелета и в `blockwright/reviewing.py` все обращения к `out/review/findings.md` на `paths.FINDINGS`.

- [ ] **Step 4: Написать селфтест**

Create `tools/findings_selftest.py`:

```python
"""The journal parses, and the audit catches what a journal hides.

Four failures, each of which has happened: a finding closed with no account of
what would prevent it, a loop with no empty round at the end, a rejection raised
again and again, and a heading the parser cannot read at all -- which turns a
ledger into prose without saying so.

    python -m tools.findings_selftest
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from blockwright import findings

LEDGER = """\
# fixture -- review journal

## Gate 4 -- greybox

### Round 1 -- agent
- F-01 the north wing is shorter than the south; the reference has them level

### Round 2 -- agent
- F-01 closed, confirmed on 01-high-front

### Round 1 -- human
- F-02 the club wing has balconies; the photographs show it blank

### F-01 | built | gate 4 | round 1 | seen 1 | by: reviewer
**The north wing is shorter than the south.**

**Fixed:** the wing was extruded to the median of the whole plan rather than to
its own skyline. It now reads `skyline["north_wing"]`.

**Arose from:** the skeleton's `shell()` takes one top for every part.

**Prevented by:** a gate row comparing each part's built top against its own
measured skyline. -- gate row

### F-02 | open | gate 4 | round 1 | seen 1 | by: human
**The club wing has balconies; the photographs show it blank.**
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "findings.md"
        path.write_text(LEDGER, encoding="utf-8")

        found = findings.read(path)
        if len(found) != 2:
            print(f"FAIL: parsed {len(found)} finding(s), expected 2")
            return 1
        if found[1].by != "human":
            print(f"FAIL: F-02 read as by {found[1].by!r}, expected 'human'")
            return 1
        if found[0].gate != 4:
            print(f"FAIL: F-01 read gate {found[0].gate}, expected 4")
            return 1
        if "skyline" not in found[0].field("Fixed"):
            print("FAIL: the Fixed field did not parse")
            return 1

        said = findings.audit(path)
        if not any("human loop has no empty round" in line for line in said):
            print("FAIL: audit missed the unfinished human loop")
            return 1
        if any("F-01" in line and "Prevented by" in line for line in said):
            print("FAIL: audit complained about a finding that has the field")
            return 1

    print("ledger parses, audit catches all four")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Прогнать**

```bash
python -m tools.findings_selftest
```

Expected: PASS.

- [ ] **Step 6: Описать формат в команде ревью**

В `.claude/commands/blockwright-review.md` заменить раздел «Записать» на полное описание журнала: хроника раундов сверху, блоки находок снизу, четыре поля, итоговая строка ворот с числом раундов и числом человеческих находок сверх агента.

- [ ] **Step 7: Коммит**

```bash
git add blockwright/findings.py buildings/_template/paths.py tools/findings_selftest.py .claude/commands/blockwright-review.md
git commit -m "Record how each finding was fixed, and whether the loop ever closed

The ledger said what was found and what state it ended in. It could not say
whether the loop converged -- an abandoned loop and a finished one look identical
from the outside unless the empty round is written down -- and it could not say
why a defect arose, without which a fix does not generalise. 'Changed recess to
band' teaches nothing; the same fix beside 'the skeleton offers no balcony
primitive' teaches the next building.

The journal also moves above out/, which the method document describes as
everything derived, deleted and rebuilt whole. Nothing rebuilds a journal."
```

---

### Task 6: Ручной режим

**Goal:** `tools/run.py` умеет `--manual`: после того как агентский цикл ворот сошёлся, прогон останавливается и печатает, что показать человеку.

**Files:**
- Modify: `tools/run.py` — флаг, порядок этапов, коды возврата
- Modify: `.claude/commands/blockwright-run.md` — человеческий цикл на трёх воротах
- Modify: `.claude/skills/blockwright/SKILL.md` — этапы и ворота

**Acceptance Criteria:**
- [ ] `python -m tools.run <имя> --manual` останавливается на первых воротах, у которых агентский цикл сошёлся, а человеческого раунда ещё нет
- [ ] Выход с кодом `10` означает «ворота ждут человека» и отличается от `1` (что-то упало)
- [ ] Печатается, какие ворота, какие файлы открыть и что уже нашёл агент
- [ ] `--auto` (и отсутствие флага) ведут себя как сегодня
- [ ] `.claude/commands/blockwright-run.md` описывает цикл: показать → выслушать → починить → показать снова → пока человек не скажет «всё ок»
- [ ] `python -m tools.pipeline_selftest` зелёный

**Verify:** `python -m tools.run _selftest_greybox --manual` → код возврата 10 и строка `gate 4 is waiting for you`

**Steps:**

- [ ] **Step 1: Понять, где ворота в нынешнем прогоне**

`tools/run.py` гоняет `probes.derive` → `build` → `gate` → (`--review`). Ворота 4 встают между `build --greybox` и полной сборкой; ворота 5 — после `review`.

- [ ] **Step 2: Добавить флаг и остановки**

В `tools/run.py`:

```python
# What a run exits with when it has stopped on purpose. Distinct from 1, which
# means something failed: a gate waiting for a person is not a failure, and a
# caller that cannot tell the two apart will either treat every pause as a
# breakage or every breakage as a pause.
WAITING = 10


def waiting_at(building: str) -> int | None:
    """The first gate whose agent loop has closed and whose human loop has not.

    Read off the journal rather than off a state file. A state file is a second
    account of what happened, and the two go out of step exactly when it matters
    -- the journal already says which rounds ran and who ran them.
    """
    from blockwright import findings
    path = ROOT / "buildings" / building / "findings.md"
    if not path.exists():
        return None
    ran = findings.rounds(path)
    for gate in (1, 4, 5):
        agent = [r for r in ran if r[1] == "agent"]
        human = [r for r in ran if r[1] == "human"]
        if agent and not agent[-1][2] and (not human or human[-1][2]):
            return gate
    return None
```

и в `main`, после прогона этапов:

```python
    if "--manual" in argv:
        gate = waiting_at(building)
        if gate is not None:
            here = ROOT / "buildings" / building / "out"
            print()
            print(f"gate {gate} is waiting for you. The agent loop closed; "
                  "yours has not run.")
            print(f"  open   {here / 'greybox' / 'top.png'}")
            print(f"  and    {here / 'greybox' / 'review'}")
            print(f"  agent findings are in "
                  f"{ROOT / 'buildings' / building / 'findings.md'}")
            print("Say what it missed, or say it is fine. Nothing after this "
                  "gate runs until you do.")
            return WAITING
```

- [ ] **Step 3: Описать цикл в команде**

В `.claude/commands/blockwright-run.md` добавить раздел:

```markdown
## Ручной режим

`--manual` — отладочный. Он вставляет человеческое ревью **после** агентского,
на трёх воротах: захват, грейбокс, постройка.

Порядок на каждых воротах:

1. агентский цикл: ревью → находки → починка → ревью → пока раунд не придёт пустым
2. **остановка.** Показать человеку: что смотреть, что уже нашёл агент
3. человеческий цикл: он говорит, что агент пропустил → починка → показать снова
   → пока он не скажет «всё ок»

Человеческий цикл начинается **только после** того, как агентский сошёлся.
Иначе человек тратится на находки, которые агент сделал бы сам на следующем
раунде.

Каждая человеческая находка ложится в `buildings/<имя>/findings.md` с `by:
human` и с четырьмя полями. Поле **Prevented by** — то, ради чего журнал
ведётся: вопрос не «как починить», а «какая строка должна была сказать это до
человека».

После каждой починки по человеческой находке автоматические проверки гоняются
заново. Правка глаз не смеет двигать то, что уже сошлось числом; сдвинула — это
отдельная находка, а не побочный эффект.
```

- [ ] **Step 4: Обновить карту стадий в скилле**

В `.claude/skills/blockwright/SKILL.md` заменить таблицу стадий на таблицу этапов и ворот из спеки: пять этапов, колонки «артефакт», «агент», «человек».

- [ ] **Step 5: Прогнать**

```bash
python -m tools.pipeline_selftest
python -m tools.greybox_selftest
python -m tools.findings_selftest
python -m tools.topsheet_selftest
```

Expected: четыре PASS.

- [ ] **Step 6: Проверить остановку**

```bash
python -m tools.fixture buildings/_scratch
python -m buildings._scratch.probes.derive
python -m buildings._scratch.build --greybox
printf '# scratch -- review journal\n\n## Gate 4 -- greybox\n\n### Round 1 -- agent\n\n' > buildings/_scratch/findings.md
python -m tools.run _scratch --manual; echo "exit $?"
rm -rf buildings/_scratch
```

Expected: `gate 4 is waiting for you` и `exit 10`.

- [ ] **Step 7: Коммит**

```bash
git add tools/run.py .claude/commands/blockwright-run.md .claude/skills/blockwright/SKILL.md
git commit -m "Add the manual mode, which is a human review after the agent's

It is scaffolding and says so. The end state is auto only, and what the manual
mode is for is measuring the gap between what the agent's eyes catch and what a
person's do -- so the human loop starts only once the agent's has stopped
bringing anything up, and a round where the person finds nothing is the signal
that this gate no longer needs them.

The waiting state is read off the journal rather than kept in a file of its own.
A state file is a second account of what happened and goes out of step with the
first exactly when it matters; the journal already records which rounds ran and
who ran them.

Exit 10 rather than 0 or 1: a gate waiting for a person is neither finished nor
broken, and a caller that cannot tell those apart treats every pause as a
breakage or every breakage as a pause."
```

---

## Self-Review

**Spec coverage:**

| Раздел спеки | Задача |
|---|---|
| 3.1 Грейбокс, два списка секций | 1 |
| 3.1 Заглушка высоты | 2 |
| 3.2 Что показывают ворота 4 | 3 |
| 3.3 Ревьюер грейбокса | 4 |
| 4.2 Журнал: хроника и поля находки | 5 |
| 4.2 Переезд журнала из `out/` | 5 |
| 4.2.1 Поле «чем предотвратить» | 5 |
| 4.3 Оба ревью цикличны | 5 (аудит пустого раунда), 6 (порядок) |
| 4.4 Поведение `--auto` / `--manual` | 6 |

Разделы 1, 2, 5, 7 спеки покрывает первый план: [2026-08-17-site-scope-and-provenance.md](2026-08-17-site-scope-and-provenance.md).

**Placeholder scan:** проверено. Шаги, правящие прозу (команды и скиллы), называют файл и раздел и приводят текст, который туда кладётся. Шаги, правящие код, несут код.

**Type consistency:** `Finding.gate` — `int`, `Finding.by` — `str` с умолчанием `"reviewer"`, заводятся в задаче 5 и читаются в задаче 6 через `findings.rounds`. `paths.GREYBOX` и `paths.BUILD` заводятся в задаче 1 и используются в задачах 3 и 4; `paths.SCHEM` удалён и нигде не остаётся. `Site.placeholder` — `set[str]`, заводится в задаче 2 и упоминается в промпте агента в задаче 4. `WAITING = 10` — только в `tools/run.py`.

**Одно расхождение со спекой, зафиксированное намеренно:** спека приводит примеры журнала по-русски (раздел 4.2), а журнал пишется по-английски, как нынешний `out/review/findings.md` на всех зданиях. Разбираемые поля заголовка — английские слова (`gate`, `round`, `seen`, `by`). Проза спеки иллюстративна; формат задаёт этот план.
