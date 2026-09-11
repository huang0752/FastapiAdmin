import AuthAPI from "@/api/module_system/auth";

const controlExchangePromises = new Map<string, ReturnType<typeof AuthAPI.controlExchange>>();

export function consumeControlCodeExchange(code: string): void {
  controlExchangePromises.delete(code);
}

export function exchangeControlCodeOnce(code: string) {
  const existing = controlExchangePromises.get(code);
  if (existing) return existing;

  const request = AuthAPI.controlExchange(code);
  controlExchangePromises.set(code, request);
  void request.catch(() => {
    if (controlExchangePromises.get(code) === request) {
      controlExchangePromises.delete(code);
    }
  });
  return request;
}
