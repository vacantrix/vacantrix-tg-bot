import { serve } from "https://deno.land/std@0.168.0/http/server.ts"
import { createClient } from "https://esm.sh/@supabase/supabase-js@2"

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
}

const SHOP_ID     = Deno.env.get("YOOKASSA_SHOP_ID")!
const SHOP_SECRET = Deno.env.get("YOOKASSA_SECRET_KEY")!
const SUPA_URL    = Deno.env.get("SUPABASE_URL")!
const SUPA_SVC    = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!

serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS })

  try {
    const { plan_id, user_id } = await req.json()
    const db = createClient(SUPA_URL, SUPA_SVC)

    const { data: plan, error: planErr } = await db
      .from("plans")
      .select("*, tools(name)")
      .eq("id", plan_id)
      .single()
    if (planErr) throw new Error("Plan not found")

    const { data: payment, error: payErr } = await db
      .from("payments")
      .insert({ user_id, plan_id, amount_rub: plan.price_rub, status: "pending" })
      .select()
      .single()
    if (payErr) throw new Error(payErr.message)

    const ykRes = await fetch("https://api.yookassa.ru/v3/payments", {
      method: "POST",
      headers: {
        "Authorization": "Basic " + btoa(`${SHOP_ID}:${SHOP_SECRET}`),
        "Content-Type": "application/json",
        "Idempotence-Key": crypto.randomUUID(),
      },
      body: JSON.stringify({
        amount: { value: plan.price_rub.toFixed(2), currency: "RUB" },
        confirmation: {
          type: "redirect",
          return_url: "https://romakotel30-cell.github.io/vacantrix-web/?payment=success",
        },
        capture: true,
        description: `Подписка ${plan.tools.name} ${plan.name}`,
        metadata: { payment_db_id: payment.id, user_id, plan_id },
      }),
    })

    const yk = await ykRes.json()
    if (!ykRes.ok) throw new Error(yk.description ?? "YooKassa error")

    await db.from("payments").update({ yookassa_id: yk.id }).eq("id", payment.id)

    return new Response(
      JSON.stringify({ payment_url: yk.confirmation.confirmation_url, payment_id: payment.id }),
      { headers: { ...CORS, "Content-Type": "application/json" } },
    )
  } catch (e) {
    return new Response(
      JSON.stringify({ error: e.message }),
      { status: 400, headers: { ...CORS, "Content-Type": "application/json" } },
    )
  }
})
