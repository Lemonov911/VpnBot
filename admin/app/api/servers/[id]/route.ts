import { NextRequest, NextResponse } from 'next/server'
import { requireSession } from '@/lib/auth'
import Database from 'better-sqlite3'
import path from 'path'

const DB_PATH = process.env.BOT_DB_PATH
  ?? path.resolve(process.cwd(), '../bot/bot.db')

function writeDb() {
  return new Database(DB_PATH)
}

/** DELETE /api/servers/[id] — hard delete a drained server.
 *
 *  Safety:
 *  - Refuses if `is_active=1` (force админ дрейнить сначала)
 *  - Refuses если на сервере есть active configs (юзеры с peer'ами,
 *    которые сломались бы при удалении — пусть subscriptions сначала
 *    истекут, агент `_sync_vless_active_uuids` подметёт peers, потом
 *    удалить)
 *  - Empty configs (status='empty') с этого server_id — nullify ссылку
 *    перед DELETE (FK constraint, чтобы не упасть)
 */
export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const session = await requireSession()
  if (!session) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })

  const { id } = await ctx.params
  const sid = parseInt(id, 10)
  if (!Number.isFinite(sid)) {
    return NextResponse.json({ error: 'invalid id' }, { status: 400 })
  }

  const db = writeDb()
  try {
    const srv = db.prepare('SELECT id, name, is_active FROM servers WHERE id=?').get(sid) as { id: number; name: string; is_active: number } | undefined
    if (!srv) {
      return NextResponse.json({ error: 'server not found' }, { status: 404 })
    }
    if (srv.is_active) {
      return NextResponse.json({
        error: 'Server is still active. Drain it first (нажми «Drain» → подожди пока новые пиры перестанут на него попадать).',
      }, { status: 400 })
    }

    // Active configs (status='active') блокируют удаление — это живые
    // пиры юзеров. Они должны истечь естественно или быть revoked сначала.
    const activeRow = db.prepare(
      "SELECT COUNT(*) AS n FROM configs WHERE server_id=? AND status='active'"
    ).get(sid) as { n: number }
    if (activeRow.n > 0) {
      return NextResponse.json({
        error: `На сервере ещё ${activeRow.n} active конфигов. Подожди их истечения или revoke вручную, потом удаляй.`,
      }, { status: 400 })
    }

    // Empty-slot configs (revoke'нутые пиры) — обнуляем server_id перед
    // DELETE servers, чтобы не нарушить FK (если PRAGMA foreign_keys=ON).
    db.prepare(
      "UPDATE configs SET server_id=NULL WHERE server_id=? AND status='empty'"
    ).run(sid)

    db.prepare('DELETE FROM servers WHERE id=?').run(sid)
    return NextResponse.json({ ok: true, deleted: srv.name })
  } finally {
    db.close()
  }
}
