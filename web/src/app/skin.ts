// 스킨 5종 — <html data-skin> + localStorage('kbj-skin'). 저장소는 막혀 있을 수 있어 늘 try/catch(미리보기와 같음).
export const SKINS = [
  { id: 'amber', label: 'Amber' },
  { id: 'phosphor', label: 'Phosphor' },
  { id: 'uv', label: 'UV' },
  { id: 'dark', label: 'Dark' },
  { id: 'white', label: 'White' },
] as const;

export type SkinId = (typeof SKINS)[number]['id'];
export const DEFAULT_SKIN: SkinId = 'amber';
export const SKIN_STORAGE_KEY = 'kbj-skin';

export function isSkin(v: unknown): v is SkinId {
  return typeof v === 'string' && SKINS.some((s) => s.id === v);
}

export function loadSkin(storage: Pick<Storage, 'getItem'> | null = safeStorage()): SkinId {
  try {
    const v = storage?.getItem(SKIN_STORAGE_KEY);
    return isSkin(v) ? v : DEFAULT_SKIN;
  } catch {
    return DEFAULT_SKIN; // 저장소 막힘(사생활 보호 모드 등) — 기본 스킨
  }
}

export function saveSkin(skin: SkinId, storage: Pick<Storage, 'setItem'> | null = safeStorage()): void {
  try {
    storage?.setItem(SKIN_STORAGE_KEY, skin);
  } catch {
    // 저장 실패는 화면에 영향이 없다(다음 방문에 기본 스킨) — 편의 기능이라 조용히 넘긴다
  }
}

export function applySkin(skin: SkinId, doc: Document = document): void {
  doc.documentElement.dataset.skin = skin;
  const meta = doc.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (meta) {
    try {
      const bg = getComputedStyle(doc.documentElement).getPropertyValue('--bg').trim();
      if (bg) meta.content = bg;
    } catch {
      // 계산 스타일을 못 읽는 환경(시험) — 테마 색은 index.html 기본값
    }
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}
