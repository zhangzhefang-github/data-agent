const KEYWORDS = new Set('WITH AS SELECT FROM WHERE AND OR NOT IN IS NULL ON JOIN LEFT RIGHT INNER OUTER FULL CROSS GROUP BY ORDER HAVING LIMIT OFFSET DISTINCT UNION ALL CASE WHEN THEN ELSE END BETWEEN LIKE ILIKE ASC DESC CAST OVER PARTITION EXISTS TRUE FALSE USING EXCEPT INTERSECT INTERVAL DATE TIME TIMESTAMP INTEGER INT BIGINT SMALLINT DOUBLE FLOAT REAL DECIMAL NUMERIC VARCHAR CHAR TEXT BOOLEAN BOOL'.split(' '))
const FUNCTIONS = new Set('SUM COUNT AVG MIN MAX ROUND COALESCE NULLIF ABS LOWER UPPER TRIM LENGTH SUBSTRING DATE_TRUNC DATE_DIFF DATE_PART EXTRACT STRFTIME ROW_NUMBER RANK DENSE_RANK LAG LEAD STRING_AGG ARRAY_AGG ANY_VALUE MEDIAN STDDEV VARIANCE GREATEST LEAST IFNULL TRY_CAST CURRENT_DATE CURRENT_TIMESTAMP NOW EPOCH'.split(' '))

export type SqlToken = { kind: 'plain' | 'keyword' | 'function' | 'string' | 'number' | 'comment', text: string }

export function highlightSql(sql: string): SqlToken[] {
  const tokens: SqlToken[] = []
  let index = 0
  const push = (kind: SqlToken['kind'], text: string) => {
    const last = tokens[tokens.length - 1]
    if (last?.kind === kind) last.text += text
    else tokens.push({ kind, text })
  }
  while (index < sql.length) {
    const rest = sql.slice(index)
    if (rest.startsWith('--')) {
      const lineEnd = rest.indexOf('\n')
      const text = lineEnd === -1 ? rest : rest.slice(0, lineEnd)
      push('comment', text)
      index += text.length
      continue
    }
    if (rest.startsWith('/*')) {
      const blockEnd = rest.indexOf('*/')
      const text = blockEnd === -1 ? rest : rest.slice(0, blockEnd + 2)
      push('comment', text)
      index += text.length
      continue
    }
    if (rest[0] === "'") {
      let cursor = 1
      while (cursor < rest.length) {
        if (rest[cursor] === "'" && rest[cursor + 1] === "'") { cursor += 2; continue }
        if (rest[cursor] === "'") { cursor += 1; break }
        cursor += 1
      }
      push('string', rest.slice(0, cursor))
      index += cursor
      continue
    }
    const word = /^[A-Za-z_][A-Za-z0-9_]*/.exec(rest)
    if (word) {
      const upper = word[0].toUpperCase()
      push(KEYWORDS.has(upper) ? 'keyword' : FUNCTIONS.has(upper) ? 'function' : 'plain', word[0])
      index += word[0].length
      continue
    }
    const number = /^\d+(?:\.\d+)?/.exec(rest)
    if (number) {
      push('number', number[0])
      index += number[0].length
      continue
    }
    push('plain', rest[0])
    index += 1
  }
  return tokens
}
