/**
 * Сверка двух реализаций Verifiable Intent.
 *
 * Берёт conformance_vectors.json, сгенерированный эталонным Python SDK
 * (Mastercard), и проверяет те же самые подписанные цепочки реализацией
 * lukasjhan/verifiable-intent на TypeScript. Затем сравнивает вердикты.
 *
 * Расхождение здесь — это и есть то, ради чего нужны контрольные примеры:
 * два независимых кода по-разному поняли один и тот же байтовый вход.
 */

import { readFileSync } from "node:fs";
import { parseLayer, verifyChain } from "verifiable-intent";
import type { L1, L2, L3Checkout, L3Payment } from "verifiable-intent";

interface Vector {
  id: string;
  title: string;
  keys: { issuer_public_jwk: Record<string, unknown> };
  artifacts: {
    layer1: string;
    layer2: string;
    layer2_payment_presentation: string;
    layer2_checkout_presentation: string;
    layer3_payment: string;
    layer3_checkout: string;
  };
  expected: {
    chain_valid: boolean;
    chain_errors: string[];
    constraints_satisfied: boolean;
    violations: string[];
  };
}

interface Doc {
  generated_at: number;
  vectors: Vector[];
}

const path = process.argv[2];
if (!path) {
  console.error("использование: run.ts <путь к conformance_vectors.json>");
  process.exit(2);
}

const doc: Doc = JSON.parse(readFileSync(path, "utf8"));

// Мандаты L3 живут 5 минут. Время проверки инъектируется, поэтому берём
// момент генерации векторов — иначе цепочки считались бы просроченными.
const now = doc.generated_at;

console.log(`векторов: ${doc.vectors.length}`);
console.log(`время проверки: ${new Date(now * 1000).toISOString()} (момент генерации)\n`);

let agree = 0;
const disagreements: string[] = [];

for (const v of doc.vectors) {
  const a = v.artifacts;
  const l1 = (await parseLayer(a.layer1)) as L1;
  const l2 = (await parseLayer(a.layer2)) as L2;
  const l3Payment = (await parseLayer(a.layer3_payment)) as unknown as L3Payment;
  const l3Checkout = (await parseLayer(a.layer3_checkout)) as unknown as L3Checkout;

  let tsValid: boolean;
  let tsErrors: string[];
  try {
    const res = await verifyChain({
      l1,
      l2,
      l3Payment,
      l3Checkout,
      l2PaymentSerialized: a.layer2_payment_presentation,
      l2CheckoutSerialized: a.layer2_checkout_presentation,
      resolveIssuerKey: async () => v.keys.issuer_public_jwk as never,
      now,
    });
    tsValid = res.valid;
    tsErrors = res.errors;
  } catch (e) {
    tsValid = false;
    tsErrors = [`исключение: ${(e as Error).message}`];
  }

  // Питон разделяет «цепочка валидна» и «ограничения соблюдены».
  // TypeScript проверяет ограничения внутри verifyChain, поэтому его
  // valid=false может означать и то, и другое. Сравниваем итог:
  // принимается ли покупка целиком.
  const pyAccepts = v.expected.chain_valid && v.expected.constraints_satisfied;
  const tsAccepts = tsValid;
  const same = tsAccepts === pyAccepts;
  if (same) agree++;
  else disagreements.push(v.id);

  console.log(
    `[${v.id.padEnd(12)}] принимает: python=${String(pyAccepts).padEnd(5)} typescript=${String(tsAccepts).padEnd(5)} ${same ? "совпало" : "РАСХОЖДЕНИЕ"}`,
  );
  if (!pyAccepts || !tsAccepts) {
    for (const e of tsErrors) console.log(`    ts: ${e}`);
    for (const e of v.expected.chain_errors) console.log(`    py (цепочка): ${e}`);
    for (const e of v.expected.violations) console.log(`    py (ограничения): ${e}`);
  }
}

console.log(`\nсовпало по валидности цепочки: ${agree}/${doc.vectors.length}`);
if (disagreements.length) {
  console.log(`расхождения: ${disagreements.join(", ")}`);
  process.exit(1);
}
