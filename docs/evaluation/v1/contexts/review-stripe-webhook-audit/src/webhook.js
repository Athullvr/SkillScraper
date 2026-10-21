export async function handleWebhook(request, processEvent) {
  const event = await request.json();
  return processEvent(event);
}
