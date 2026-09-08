import mitt from "mitt"

export interface RevokeOpenPayload {
  pluginId: string
  provider: string
  onConfirm: (pluginId: string, provider: string) => Promise<void>
}

type RevokeEvents = {
  "revoke:open": RevokeOpenPayload
}

const revokeEmitter = mitt<RevokeEvents>()

export default revokeEmitter
