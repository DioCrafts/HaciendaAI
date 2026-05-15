import { useEffect, useState } from "react";
import type { TaxProfile } from "../types";

const STORAGE_KEY = "hacienda-ai:profile";

function loadFromStorage(initial: TaxProfile): TaxProfile {
  if (typeof window === "undefined") return initial;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return initial;
    const parsed = JSON.parse(raw) as TaxProfile;
    return { ...initial, ...parsed };
  } catch {
    return initial;
  }
}

/**
 * Persiste el perfil fiscal en localStorage. Lee al montar y escribe
 * en cada cambio. La clave es estable (`hacienda-ai:profile`) y se
 * puede limpiar con `clearStoredProfile()`.
 */
export function useProfilePersistence(initial: TaxProfile): [TaxProfile, (profile: TaxProfile) => void] {
  const [profile, setProfile] = useState<TaxProfile>(() => loadFromStorage(initial));

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(profile));
    } catch {
      // localStorage puede estar deshabilitado; el perfil se mantiene en memoria.
    }
  }, [profile]);

  return [profile, setProfile];
}

export function clearStoredProfile(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}
