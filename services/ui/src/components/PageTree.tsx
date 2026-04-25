/**
 * Compact page tree visualization for the scrape monitor.
 *
 * Builds a parent → children tree from the flat page list (each page records
 * its parent_url). Shows depth indentation, status indicator, and title.
 */
import { useMemo } from 'react'
import type { ScrapePage } from '@/api/client'

interface PageTreeProps {
  pages: ScrapePage[]
}

interface TreeNode {
  page: ScrapePage
  children: TreeNode[]
}

function buildTree(pages: ScrapePage[]): TreeNode[] {
  const byUrl = new Map<string, TreeNode>()
  for (const p of pages) {
    byUrl.set(p.url, { page: p, children: [] })
  }
  const roots: TreeNode[] = []
  for (const node of byUrl.values()) {
    const parent = node.page.parent_url ? byUrl.get(node.page.parent_url) : null
    if (parent) {
      parent.children.push(node)
    } else {
      roots.push(node)
    }
  }
  // Sort children by URL for stable display
  const sortRec = (n: TreeNode) => {
    n.children.sort((a, b) => a.page.url.localeCompare(b.page.url))
    n.children.forEach(sortRec)
  }
  roots.sort((a, b) => a.page.url.localeCompare(b.page.url))
  roots.forEach(sortRec)
  return roots
}

function shortUrl(url: string): string {
  try {
    const u = new URL(url)
    const path = u.pathname + (u.search || '')
    return path === '/' ? u.host : path
  } catch {
    return url
  }
}

function NodeView({ node, depth = 0 }: { node: TreeNode; depth?: number }) {
  const status = node.page.status
  const dotColor =
    status === 'downloaded' ? 'bg-success' :
    status === 'failed' ? 'bg-error' :
    'bg-base-content/30'

  return (
    <li className="leading-tight">
      <div className="flex items-baseline gap-2 py-0.5" style={{ paddingLeft: `${depth * 14}px` }}>
        <span className={`size-1.5 rounded-full shrink-0 mt-1.5 ${dotColor}`} />
        <span className="text-xs font-mono truncate min-w-0" title={node.page.url}>
          {shortUrl(node.page.url)}
        </span>
        {node.page.title && (
          <span className="text-[10px] text-base-content/40 truncate hidden sm:inline">
            {node.page.title}
          </span>
        )}
      </div>
      {node.children.length > 0 && (
        <ul className="list-none">
          {node.children.map((child) => (
            <NodeView key={child.page.id} node={child} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  )
}

export default function PageTree({ pages }: PageTreeProps) {
  const tree = useMemo(() => buildTree(pages), [pages])

  if (pages.length === 0) {
    return (
      <div className="text-xs text-base-content/40 text-center py-4">
        No pages discovered yet
      </div>
    )
  }

  return (
    <ul className="list-none max-h-72 overflow-y-auto bg-base-200/50 rounded-xl p-3">
      {tree.map((root) => (
        <NodeView key={root.page.id} node={root} />
      ))}
    </ul>
  )
}
