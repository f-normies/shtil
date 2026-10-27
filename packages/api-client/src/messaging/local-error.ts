export class MessagingLocalError extends Error {
  constructor(
    readonly code: string,
    message = code,
  ) {
    super(message);
    this.name = "MessagingLocalError";
  }
}
