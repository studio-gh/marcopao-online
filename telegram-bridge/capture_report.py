async def latest_incoming(client, entity):
    items = await client.get_messages(entity, limit=100)
    incoming = [message for message in items if not message.out]
    return incoming[0] if incoming else None
async def latest_incoming(client, entity):
    items = await client.get_messages(entity, limit=100)
    incoming = [message for message in items if not message.out]
    return max(incoming, key=lambda message: message.id) if incoming else None