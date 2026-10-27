/**
 * Защита от двойной отправки: `isPending` мутации обновляется после перерисовки, второй
 * щелчок двойного клика успевает раньше. `take()` — можно ли отправлять сейчас.
 */
export function createSubmitGuard(): { take: () => boolean; release: () => void } {
  let busy = false;
  return {
    take: () => {
      if (busy) return false;
      busy = true;
      return true;
    },
    release: () => {
      busy = false;
    },
  };
}
