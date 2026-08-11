"""What was supplied, what each thing is allowed to decide, and what is left over
to grade the result against.

A build is only as honest as the bookkeeping of its evidence, and that bookkeeping
is the same on every job: survey what is in `input/`, give each question to the
strongest source that can answer it, and keep whatever is left as a witness. Doing
that by hand once per building is how a pipeline ends up measuring a height off a
photograph on a Tuesday.

Three ideas, and everything here is one of them.

**Kind.** Seven kinds of input turn up, and they differ in what they can be
trusted for far more than in what they contain. A photograph and a photogrammetry capture
are both pictures of the same building and they are authoritative for disjoint
sets of facts.

**Role.** Each of the questions a build has to answer -- where is it in plan, how
tall is it, what shape are its surfaces, what is it made of, what is its rhythm,
which parts does it have -- is answered by exactly one source: the strongest one
present that is authoritative for that question. Every other source that could
have answered it is then a *witness* for it, and grading happens against witnesses.

**Tier.** With no metric source at all, nothing is measured and every dimension is
a declaration. That is a legitimate way to build -- somebody describes a building
in a sentence and it gets built -- but it must not be reported the same way as a
build set out on a survey. The tier says which of the two happened, and the gate
reads it before deciding whether "pass" is a thing it is entitled to print.

The rule the whole pipeline turns on is that **a build's resemblance to the real
thing is graded against an input it was never given**. `witnesses_for` is that
rule made checkable.

It is about resemblance and only about resemblance. Whether the build *conforms
to its own plan* is a separate question with a separate witness -- the plan --
and checking it is verification rather than tautology; `docs/sources.md`,
"Сходство и соответствие", is where the two are told apart. A row that answers
the second and reads as though it answered the first is the failure to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# The questions a build has to answer. Ordered the way they are decided: the plan
# fixes where everything is before any of the rest can be placed.
QUESTIONS = ("plan", "heights", "shape", "material", "rhythm", "parts")

ASKED = {
    "plan": "plan: where it sits, how big, which way it points",
    "heights": "heights: storeys, levels, the skyline",
    "shape": "shape: curves, surfaces of revolution, anything not a box",
    "material": "material and colour",
    "rhythm": "rhythm: the bay module, how a facade is divided",
    "parts": "which parts exist at all",
}


@dataclass(frozen=True)
class Kind:
    """One kind of input, and the questions it is and is not a witness to.

    `authority` maps a question to how much weight this kind carries on it:

        2   measured -- this kind can be read for a number, and the number is
            the answer.
        1   declared -- this kind states the fact but not a dimension. Somebody
            reads it and writes down what they saw, with the source named.
        0   silent -- this kind says nothing about that question, or says
            something that must not be believed.

    A kind with authority 2 on a question is metric for it. A kind with authority
    1 can still settle *which parts exist*, which is the single most under-used
    fact in this pipeline, and cannot settle how big any of them is.
    """

    name: str
    label: str
    authority: dict[str, int]
    caveat: str = ""

    def metric(self, question: str) -> bool:
        return self.authority.get(question, 0) >= 2

    def speaks(self, question: str) -> bool:
        return self.authority.get(question, 0) >= 1


# The kinds, with the authority table stated once, here, rather than argued
# about per building. Read down a column to see who may answer a question and
# read across a row to see what one input is worth.
#
#                             plan  heights  shape  material  rhythm  parts
#   vector (GeoJSON/SVG)       2       0       2       0        0       2
#   brief (words)              1       1       1       1        1       1
#   map (1 px = 1 m)           2       0       2       0        0       2
#   drawing, to scale          2       2       1       1        2       1
#   drawing, no scale          1       1       1       1        1       1
#   model (a real 3D model)    2       2       2       1        1       1
#   capture (Google Earth)     1       2       0       1        1       1
#   photographs                0       0       1       2        2       2
#
# The map's twos are twos in plan and only in plan: `plan.decompose` cuts the
# drawing along its own lines and `plan.Part` fits a circle to each piece and
# reports the residual, so "how many parts and which of them are round" is a
# measurement here and not a reading. It says nothing about a section.
#
# The one zero worth arguing about is photographs on plan and on heights. A
# photograph of a building plainly says something about how big it is -- and
# every number anybody has ever read off one came with a guessed camera, a
# guessed lens and a guessed standing point. Declaring a dimension "from the
# photographs" is the single most common way a wrong number enters a build with
# a source beside it, which is worse than a wrong number without one.
KINDS = {
    "brief": Kind(
        "brief", "a written description",
        {"plan": 1, "heights": 1, "shape": 1, "material": 1, "rhythm": 1,
         "parts": 1},
        "The one input that need not describe anything that exists. Numbers "
        "taken from it are declarations and are recorded as declarations."),
    "vector": Kind(
        "vector", "a vector plan (GeoJSON or SVG)",
        {"plan": 2, "shape": 2, "parts": 2},
        "An outline that arrived as an outline: no palette, no anti-aliasing, "
        "no line layer to subtract. Exact for the plan and silent about "
        "everything above it -- a polygon has no height."),
    "map": Kind(
        "map", "a flat map, 1 pixel to 1 metre",
        {"plan": 2, "shape": 2, "parts": 2},
        "A drawing, not a survey: its edges wander by a metre or two and its "
        "interior lines are freehand. Read once for the frame and the parts, "
        "and set aside."),
    "drawing": Kind(
        "drawing", "a drawing that carries a scale",
        {"plan": 2, "heights": 2, "shape": 1, "material": 1, "rhythm": 2,
         "parts": 1},
        "Authoritative for exactly the projection it draws: an elevation for "
        "heights and rhythm, a plan for the plan. Read by a person and turned "
        "into declarations with the sheet named."),
    "sketch": Kind(
        "sketch", "a sketch, or a drawing with no scale",
        {"plan": 1, "heights": 1, "shape": 1, "material": 1, "rhythm": 1,
         "parts": 1},
        "Holds proportion and order, holds no absolute size. Scaling it by one "
        "guessed dimension multiplies that guess through the whole building."),
    "model": Kind(
        "model", "a full 3D model",
        {"plan": 2, "heights": 2, "shape": 2, "material": 1, "rhythm": 1,
         "parts": 1},
        "The only input authoritative for shape. An untextured model says "
        "nothing about material, and that silence is not the colour grey."),
    "capture": Kind(
        "capture", "a Google Earth capture",
        {"plan": 1, "heights": 2, "material": 1, "rhythm": 1, "parts": 1},
        "Photogrammetry: windows are painted on the wall, trees are fused to "
        "the facade, the surface is noisy by half a metre. Reliable for bulk "
        "and height and nothing else. It was flown, so it never saw under a "
        "roof or a canopy."),
    "photos": Kind(
        "photos", "photographs",
        {"shape": 1, "material": 2, "rhythm": 2, "parts": 2},
        "They settle what exists. They settle no dimension at all."),
}

# Who wins a tie, best first. Written out per question rather than left to the
# order of `KINDS`, because the two are not the same order and never will be: a
# capture and a model are both metric for heights, and a capture and a map are
# both worth listening to about parts, and which one wins is a judgement about
# that question and not a global ranking of inputs.
#
# A kind absent from a question's list sorts last among equals, which is the
# safe direction: it can still answer when nothing better is present.
PREFER = {
    # A map is a drawing but it is a drawing *of the plan*, made to be measured;
    # a scaled drawing is metric too but reaches the build through a person's
    # hands, and a model can be read for the same fact without either.
    "plan": ("vector", "model", "map", "drawing", "capture", "sketch", "brief"),
    # A model states a height. A capture measures one off the real thing, and
    # rounds it. A drawing's elevation is exact for what it draws. The model
    # comes first because when both a model and a capture are present, the
    # capture is worth far more as the witness than as the source.
    "heights": ("model", "drawing", "capture", "sketch", "brief"),
    # Only a model is authoritative for shape in three dimensions. A map settles
    # shape in plan -- `plan.Part` fits a circle and reports its residual -- and
    # that beats anything read off a picture.
    "shape": ("model", "vector", "map", "drawing", "sketch", "photos", "capture"),
    "material": ("photos", "drawing", "model", "sketch", "capture", "brief"),
    "rhythm": ("drawing", "photos", "sketch", "model", "capture", "brief"),
    # What exists is a photograph's strongest suit; after that, what somebody
    # drew a line around.
    "parts": ("photos", "vector", "map", "drawing", "model", "sketch", "capture",
              "brief"),
}


@dataclass(frozen=True)
class Source:
    """One kind of input, where it actually is, and what this copy of it lacks.

    `override` lowers the kind's authority for this particular file. The kind
    says what that sort of input is worth in general; a given file can be worth
    less, and the only honest place to say so is beside the file. An untextured
    model is the case this exists for: its kind is authoritative for material at
    weight 1, and a model with no materials on it says nothing about material at
    all -- and the silence is not the colour grey.

    It only ever lowers. A file cannot be worth more than its kind: a photograph
    with a ruler in it still does not carry a survey.
    """

    kind: Kind
    path: Path
    note: str = ""
    override: dict[str, int] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.kind.name

    def weight(self, question: str) -> int:
        return min(self.kind.authority.get(question, 0),
                   self.override.get(question, 9))

    def metric(self, question: str) -> bool:
        return self.weight(question) >= 2

    def speaks(self, question: str) -> bool:
        return self.weight(question) >= 1

    def __str__(self) -> str:
        return f"{self.kind.label} ({self.path.name})"


@dataclass
class Evidence:
    """The survey: what is here, who answers what, and what is left to grade with.

    Built by `survey`. `derive` writes it into `derived.json` so that every later
    stage reads the same account of the evidence rather than re-deciding it, and
    so that a run a month old still says what it was set out on.
    """

    sources: list[Source] = field(default_factory=list)

    def of(self, name: str) -> Source | None:
        for source in self.sources:
            if source.name == name:
                return source
        return None

    def answers(self, question: str) -> Source | None:
        """The source that decides a question: the strongest one present.

        Weight first, then `PREFER`. Ties are the normal case rather than a
        coincidence -- two inputs claiming the same authority over the same
        question happens on every well-supplied job -- so the tie-break is a
        stated list and not the order this file happens to declare its kinds in.
        An implicit tie-break by declaration order was what once gave heights to
        a photogrammetry capture while a clean model of the same building sat
        beside it unused.
        """
        order = PREFER.get(question, ())
        def rank(source: Source) -> tuple[int, int]:
            place = order.index(source.name) if source.name in order else len(order)
            return (source.weight(question), -place)

        best = max(self.sources, key=rank, default=None)
        if best is None or not best.weight(question):
            return None
        return best

    def witnesses_for(self, question: str) -> list[Source]:
        """Every source that could have answered a question but was not asked.

        These are what the answer is graded against. A question with no witness
        is a question whose answer nothing can contradict, which is not the same
        as a question answered correctly -- and the gate must say so out loud
        rather than count it as a pass.
        """
        chosen = self.answers(question)
        return [s for s in self.sources
                if s is not chosen and s.speaks(question)]

    @property
    def tier(self) -> str:
        """How much of this build is measured, in one word.

            measured    both the plan and the heights come off a metric source.
            partial     one of the two does; the other is declared.
            declared    neither does. Every dimension is somebody's statement.
        """
        metric = sum(bool(self.answers(q) and self.answers(q).metric(q))
                     for q in ("plan", "heights"))
        return ("declared", "partial", "measured")[metric]

    @property
    def reference(self) -> Source | None:
        """The geometry the gate's section is cut against, if there is any.

        Only an OBJ will do -- a model or a capture. A section compares what
        stands at every station across the building against what the reference
        has at the same station, and no other kind of input holds a height at a
        station: a photograph does not, and a map holds only the plan.

        Where both are present, the one that did **not** supply the plan is
        chosen, even though it is the weaker file. A capture is noisy and its
        windows are painted on -- and cutting a section against it is a real
        outside check, while cutting one against the model the build was traced
        from grades the tracing. The whole pipeline turns on being graded by
        something it was not given, and here that is worth more than precision.
        """
        objs = [s for s in self.sources if s.name in ("model", "capture")]
        if not objs:
            return None
        plan = self.answers("plan")
        held_out = [s for s in objs if s is not plan]
        if held_out:
            # Prefer a model over a capture among the held-out ones: it is the
            # better witness when both were held out, which happens when the
            # plan came off a map.
            return next((s for s in held_out if s.name == "model"), held_out[0])
        return objs[0]

    @property
    def independent(self) -> bool:
        """Whether that reference is a witness or the source itself.

        The section is worth most when the plan came from somewhere else -- the
        map drew the footprint, the capture is asked where material actually
        stands -- because then the two disagree for real reasons.

        When the same OBJ supplied both, the section still earns its place: it
        grades the simplification, and a part whose roof slopes or whose wing
        steps will fail it. What it cannot do is catch the reference being wrong,
        because nothing here was held back to contradict it. The gate says which
        of the two it ran, and never lets the second be read as the first.
        """
        ref = self.reference
        return bool(ref) and self.answers("plan") is not ref

    @property
    def graded(self) -> bool:
        """Whether anything here can grade the massing at all."""
        return self.reference is not None

    def lines(self) -> list[str]:
        """The survey, as a person reads it. Printed by `derive` on every run."""
        out = [f"evidence: {self.tier}"]
        for source in self.sources:
            can = ", ".join(q for q in QUESTIONS if source.metric(q))
            out.append(f"  {source.kind.label:34s} {source.path}")
            if can:
                out.append(f"  {'':34s} measures: {can}")
            if source.note:
                out.append(f"  {'':34s} {source.note}")
        out.append("")
        for question in QUESTIONS:
            answer = self.answers(question)
            if answer is None:
                out.append(f"  {ASKED[question]:52s} NOTHING ANSWERS IT")
                continue
            how = "measured" if answer.metric(question) else "declared"
            witnesses = ", ".join(w.kind.name for w in self.witnesses_for(question))
            out.append(f"  {ASKED[question]:52s} {how} from {answer.kind.name}"
                       + (f", checked against {witnesses}" if witnesses else
                          ", NOTHING TO CHECK IT AGAINST"))
        out.append("")
        if self.reference is None:
            out.append("  no model and no capture: there is nothing to cut a "
                       "section against, and the gate will say ungraded rather "
                       "than pass")
        elif self.independent:
            out.append(f"  the section is cut against the {self.reference.name}, "
                       "which is not what the plan was drawn from")
        else:
            out.append(f"  the section is cut against the {self.reference.name}, "
                       "which is also where the plan came from: that grades the "
                       "simplification, not the building")
        return out

    def to_json(self) -> dict:
        return {
            "tier": self.tier,
            "graded": self.graded,
            "reference": self.reference.name if self.reference else None,
            "independent": self.independent,
            "have": {s.name: str(s.path) for s in self.sources},
            "answers": {
                q: {
                    "by": self.answers(q).name if self.answers(q) else None,
                    "how": ("measured"
                            if self.answers(q) and self.answers(q).metric(q)
                            else "declared" if self.answers(q) else None),
                    "witnesses": [w.name for w in self.witnesses_for(q)],
                }
                for q in QUESTIONS
            },
        }


def _has_files(directory: Path, suffixes: tuple[str, ...] = ()) -> bool:
    if not directory.is_dir():
        return False
    return any(p.is_file() and (not suffixes or p.suffix.lower() in suffixes)
               for p in directory.iterdir())


def _textured(obj: Path) -> bool:
    """Whether an OBJ carries any material at all.

    Read from the file rather than assumed from the kind, because the kind
    cannot know: a model exported with materials and a model exported as bare
    geometry are the same sort of input and are worth different things. Checked
    by looking for an `mtllib` that exists and mentions a colour or a texture --
    an .mtl of nothing but names is what a stripped export leaves behind.
    """
    try:
        with open(obj, "r", encoding="utf-8", errors="replace") as handle:
            libs = [line.split(maxsplit=1)[1].strip()
                    for line in handle
                    if line.startswith("mtllib ") and len(line.split()) > 1]
    except OSError:
        return False
    for lib in libs:
        candidate = obj.parent / lib
        if not candidate.is_file():
            continue
        text = candidate.read_text(encoding="utf-8", errors="replace")
        if "map_Kd" in text or "Kd " in text:
            return True
    return False


def survey(paths) -> Evidence:
    """Read a building's `paths` module and report what was actually supplied.

    Takes the module rather than a directory because a building may put its
    inputs wherever it likes, and `paths.py` is already the one place that says
    where. Missing attributes are treated as missing inputs, so a building that
    predates a kind does not crash on it.
    """
    def at(attribute: str) -> Path | None:
        value = getattr(paths, attribute, None)
        return Path(value) if value is not None else None

    found: list[Source] = []

    def add(kind: str, path: Path | None, present: bool, note: str = "",
            override: dict[str, int] | None = None) -> None:
        if path is not None and present:
            found.append(Source(KINDS[kind], path, note, override or {}))

    brief = at("BRIEF")
    add("brief", brief, bool(brief and brief.is_file()))

    # A vector plan is one file under either of two names, because GeoJSON and
    # SVG are the same kind of input and differ only in what wrote them.
    vector = at("VECTOR")
    if vector is None or not vector.is_file():
        vector = at("VECTOR_SVG")
    add("vector", vector, bool(vector and vector.is_file()))

    layout = at("LAYOUT")
    add("map", layout, bool(layout and layout.is_file()))

    # Drawings are split by whether they carry a scale, because that is the whole
    # difference in what they are worth, and nothing in the file can tell us:
    # a scale bar is a picture. So the split is by folder, and putting a drawing
    # in the wrong one is a claim somebody made on purpose.
    scaled = at("DRAWINGS")
    add("drawing", scaled, _has_files(scaled or Path("."),
                                      (".png", ".jpg", ".jpeg", ".pdf")))
    sketches = at("SKETCHES")
    add("sketch", sketches, _has_files(sketches or Path("."),
                                       (".png", ".jpg", ".jpeg", ".pdf")))

    model = at("MODEL")
    if model is not None and model.is_file():
        bare = not _textured(model)
        add("model", model, True,
            note=("no materials on it, so it states no colour"
                  if bare else ""),
            override={"material": 0} if bare else None)

    mesh = at("MESH")
    add("capture", mesh, bool(mesh and mesh.is_file()))

    photos = at("PHOTOS")
    add("photos", photos, _has_files(photos or Path("."),
                                     (".png", ".jpg", ".jpeg", ".webp")))

    return Evidence(found)


def refuse(evidence: Evidence) -> str | None:
    """Why this survey cannot be built from, or None if it can.

    One case only: nothing at all was supplied. Everything else is buildable at
    some tier, and the tier is reported rather than used as a reason to stop --
    a building described in a sentence is a legitimate job, and refusing it
    because nobody had a survey would be the pipeline mistaking its own
    convenience for rigour.
    """
    if not evidence.sources:
        return ("there is nothing in input/. This pipeline runs off any set of "
                "inputs -- a single written description in brief.md is enough -- "
                "but it needs at least one. See docs/sources.md.")
    if evidence.answers("plan") is None:
        return ("nothing here answers the question of the plan. A photograph "
                "says what a building is made of and what parts it has, and "
                "states no dimension at all, so nothing can be set out from it. "
                "Describe the siting in brief.md: that is a declaration, and it "
                "will be recorded as one.")
    return None


__all__ = ["Evidence", "Kind", "Source", "KINDS", "QUESTIONS", "refuse", "survey"]
