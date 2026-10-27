const STOP_TIMEOUT_MS = 5_000;

function signalChild(child, signal) {
  try {
    if (child.pid && process.platform !== "win32") process.kill(-child.pid, signal);
    else child.kill(signal);
  } catch (error) {
    try {
      child.kill(signal);
    } catch (killError) {
      if (killError.code !== "ESRCH") throw killError;
      if (error.code !== "ESRCH") throw error;
    }
  }
}

function closesWithin(closed, timeoutMs) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    closed.then(() => {
      clearTimeout(timer);
      resolve(true);
    });
  });
}

export class ProcessLifecycle {
  #children = new Map();
  #interruptedBy = null;
  #installed = false;
  #sigint = () => this.interrupt("SIGINT");
  #sigterm = () => this.interrupt("SIGTERM");

  get interruptedBy() {
    return this.#interruptedBy;
  }

  install() {
    if (this.#installed) return;
    process.on("SIGINT", this.#sigint);
    process.on("SIGTERM", this.#sigterm);
    this.#installed = true;
  }

  uninstall() {
    if (!this.#installed) return;
    process.off("SIGINT", this.#sigint);
    process.off("SIGTERM", this.#sigterm);
    this.#installed = false;
  }

  track(child) {
    if (this.#children.has(child)) return;
    const entry = {
      child,
      closed: new Promise((resolve) => child.once("close", resolve)),
      stopping: null,
    };
    this.#children.set(child, entry);
    child.once("close", () => this.#children.delete(child));
    if (this.#interruptedBy) {
      void this.stop(child, this.#interruptedBy).catch((error) => process.emitWarning(error));
    }
  }

  throwIfInterrupted() {
    if (this.#interruptedBy) throw new Error(`Interrupted by ${this.#interruptedBy}`);
  }

  interrupt(signal) {
    if (this.#interruptedBy) return;
    this.#interruptedBy = signal;
    void this.stopAll(signal).catch((error) => process.emitWarning(error));
  }

  async stop(child, signal = "SIGTERM") {
    const entry = this.#children.get(child);
    if (!entry) return;
    entry.stopping ??= this.#stopEntry(entry, signal);
    await entry.stopping;
  }

  async stopAll(signal = "SIGTERM") {
    await Promise.all([...this.#children.keys()].map((child) => this.stop(child, signal)));
  }

  async #stopEntry(entry, signal) {
    const { child, closed } = entry;
    if (child.exitCode === null && child.signalCode === null) signalChild(child, signal);
    if (await closesWithin(closed, STOP_TIMEOUT_MS)) return;
    signalChild(child, "SIGKILL");
    await closed;
  }
}
