const CREDIT_CODE_ALPHABET = "0123456789ABCDEFGHJKLMNPQRTUWXY";
const CREDIT_CODE_WEIGHTS = [1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28];

export function normalizeCreditCode(value?: string | null): string | undefined {
  const normalized = value?.trim().toUpperCase();
  return normalized || undefined;
}

export function validateCreditCode(value?: string | null): boolean {
  const normalized = normalizeCreditCode(value);
  if (!normalized) return true;
  if (normalized.length !== 18) return false;

  const values = [...normalized].map((character) => CREDIT_CODE_ALPHABET.indexOf(character));
  if (values.some((value) => value < 0)) return false;

  const weightedSum = values
    .slice(0, 17)
    .reduce((sum, value, index) => sum + value * CREDIT_CODE_WEIGHTS[index]!, 0);
  const expectedCheckValue = (31 - (weightedSum % 31)) % 31;
  return values[17] === expectedCheckValue;
}
