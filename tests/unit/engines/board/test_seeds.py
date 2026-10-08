# 원본(ET) 시험 본문은 타입 표시 없는 dict 를 그대로 쓴다 — 본문을 고치지 않고 이 파일만 완화한다.
# pyright: reportArgumentType=false
"""
themes.yaml 시드 해결 — board/engine/themes.py.

매일 "시드 35건이 상장 마스터에 없습니다" 만 뜨고 있었다. 그중 다섯은 애초에
비상장이라 영원히 안 붙는다. 합쳐 세면 진짜 오타·사명변경이 그 숫자에 묻힌다.

KBJ P3 묶음 E1 — ET `board/tests/test_seeds.py` 의 Renames·Unresolved·RealFile(10) —
BannerWording(web.render)은 legacy 에 남김. 사전 경로는 config/knowledge/themes.yaml — 에서
승격했다(import 경로만 바꿨다, docs/p3_design.md §1.3·§1.11). 대상 모듈 `kbj.engines.board`.
"""

import unittest

import yaml

from kbj.engines.board import themes as TH
from kbj.engines.board.config import knowledge_path

CFG = dict(themes=dict(seed_confidence=0.9))
UNIVERSE = {"000001": dict(name="한전기술"), "000002": dict(name="두산에너빌리티")}


def ty(seeds, unlisted=None):
    d = dict(
        axes={"project_chain": dict(stages=["설계", "주기기"])},
        themes=[dict(id="nuclear", name="원전", axis="project_chain", seeds=seeds)],
    )
    if unlisted:
        d["unlisted"] = unlisted
    return d


class Renames(unittest.TestCase):
    """사명이 바뀐 시드는 표를 거쳐 붙는다.

    한국 상장사는 이름을 자주 바꾼다(한국조선해양 → HD한국조선해양). 시드는
    이름으로 적혀 있어서, 바뀌는 족족 '마스터에 없습니다' 로 매일 뜬다.
    옛 이름을 지우지 않고 표로 옮기는 이유는 근거를 함께 남기기 위해서다.
    """

    UNI = {"000001": dict(name="HD한국조선해양"), "000002": dict(name="HL만도")}  # noqa: RUF012 — 원본 그대로(승격)

    def test_renamed_seed_maps(self):
        d = ty({"설계": ["한국조선해양"]})
        d["renames"] = {"한국조선해양": "HD한국조선해양"}
        m, _, un = TH.build(d, self.UNI, CFG)
        self.assertIn("000001", m)
        self.assertEqual(un, [])

    def test_rename_target_must_exist(self):
        # 표에 적었는데 그 이름도 마스터에 없으면 조용히 붙이면 안 된다.
        d = ty({"설계": ["한국조선해양"]})
        d["renames"] = {"한국조선해양": "없는회사"}
        m, _, un = TH.build(d, self.UNI, CFG)
        self.assertEqual(m, {})
        self.assertEqual(len(un), 1)
        self.assertEqual(un[0]["seed"], "한국조선해양")

    def test_direct_match_wins_over_rename(self):
        # 지금 이름으로 그대로 붙는 시드는 표를 타지 않는다.
        d = ty({"설계": ["HL만도"]})
        d["renames"] = {"HL만도": "HD한국조선해양"}
        m, _, _ = TH.build(d, self.UNI, CFG)
        self.assertIn("000002", m)

    def test_real_rename_table_targets_are_sane(self):
        # 실제 themes.yaml 의 표가 자기 자신을 가리키거나 비어 있지 않은지.
        real = yaml.safe_load(open(knowledge_path("themes.yaml"), encoding="utf-8"))
        for old, new in (real.get("renames") or {}).items():
            self.assertTrue(new and new.strip(), old)
            self.assertNotEqual(TH.norm(old), TH.norm(new), old)


class Unresolved(unittest.TestCase):
    def test_listed_seeds_map(self):
        m, meta, un = TH.build(ty({"설계": ["한전기술"]}), UNIVERSE, CFG)
        self.assertIn("000001", m)
        self.assertEqual(un, [])
        self.assertEqual(meta["nuclear"]["n_mapped"], 1)

    def test_unknown_seed_is_reported_without_a_reason(self):
        _, _, un = TH.build(ty({"설계": ["없는회사"]}), UNIVERSE, CFG)
        self.assertEqual(len(un), 1)
        self.assertIsNone(un[0]["known"])
        self.assertEqual(un[0]["seed"], "없는회사")

    def test_declared_unlisted_seed_carries_its_reason(self):
        _, _, un = TH.build(ty({"주기기": ["세메스"]}, {"세메스": "비상장"}), UNIVERSE, CFG)
        self.assertEqual(len(un), 1)  # 여전히 미해결이다 — 숨기지 않는다
        self.assertEqual(un[0]["known"], "비상장")

    def test_the_two_kinds_are_separable(self):
        _, _, un = TH.build(
            ty({"주기기": ["세메스", "오타회사"]}, {"세메스": "비상장"}), UNIVERSE, CFG
        )
        unknown = [x for x in un if not x.get("known")]
        self.assertEqual([x["seed"] for x in unknown], ["오타회사"])


class RealFile(unittest.TestCase):
    """실제 themes.yaml 이 이 계약을 지키는지 본다."""

    def setUp(self):
        p = knowledge_path("themes.yaml")
        with open(p, encoding="utf-8") as f:
            self.y = yaml.safe_load(f)

    def test_unlisted_entries_have_a_reason(self):
        for name, why in (self.y.get("unlisted") or {}).items():
            self.assertTrue(
                str(why).strip(), f"{name} 에 사유가 없다 — 사유 없는 면제는 그냥 은폐다"
            )

    def test_unlisted_names_actually_appear_in_seeds(self):
        # 시드에서 지웠는데 면제만 남으면, 다음 사람이 그 이름이 어디 쓰이는지
        # 찾다가 시간을 버린다.
        seeds = set()
        for t in self.y.get("themes") or []:
            ss = t.get("seeds") or {}
            if isinstance(ss, list):
                ss = {"기타": ss}
            for names in ss.values():
                seeds.update(names or [])
        for name in self.y.get("unlisted") or {}:
            self.assertIn(name, seeds, f"{name} 은 seeds 에 없다")


if __name__ == "__main__":
    unittest.main()
