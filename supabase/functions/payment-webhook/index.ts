import { serve } from "https://deno.land/std@0.168.0/http/server.ts"
import { createClient } from "https://esm.sh/@supabase/supabase-js@2"

const SUPA_URL = Deno.env.get("SUPABASE_URL")!
const SUPA_SVC = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!

serve(async (req) => {
  try {
    const body = await req.json()

    if (body.event !== "payment.succeeded") {
      return new Response("OK", { status: 200 })
    }

    const { payment_db_id, user_id, plan_id } = body.object.metadata
    const db = createClient(SUPA_URL, SUPA_SVC)

    await db
      .from("payments")
      .update({ status: "succeeded", paid_at: new Date().toISOString(), yookassa_id: body.object.id })
      .eq("id", payment_db_id)

    const { data: plan } = await db
      .from("plans")
      .select("duration_days, tool_id")
      .eq("id", plan_id)
      .single()
    if (!plan) throw new Error("Plan not found")

    const { data: existing } = await db
      .from("subscriptions")
      .select("expires_at")
      .eq("user_id", user_id)
      .eq("tool_id", plan.tool_id)
      .maybeSingle()

    const base = existing?.expires_at && new Date(existing.expires_at) > new Date()
      ? new Date(existing.expires_at)
      : new Date()
    base.setDate(base.getDate() + plan.duration_days)

    await db.from("subscriptions").upsert(
      { user_id, tool_id: plan.tool_id, plan_id, expires_at: base.toISOString(), status: "active", source: "launcher" },
      { onConflict: "user_id,tool_id" },
    )

    return new Response("OK", { status: 200 })
  } catch (e) {
    console.error(e)
    return new Response("Error", { status: 500 })
  }
})
