import { ftsQuery, normalizeTag, validateLabels } from '../src/api';
import { pbkdf2Hex } from '../src/auth';
import { timingSafeEqual } from '../src/http';

describe('ftsQuery', () => {
  it('matches the Python rules', () => {
    expect(ftsQuery('agents memory')).toBe('"agents"* AND "memory"*');
    expect(ftsQuery('"exact phrase" mcp')).toBe('"exact phrase" AND "mcp"*');
    expect(ftsQuery('claude-code')).toBe('"claude code"');
    expect(ftsQuery('***')).toBe('');
    expect(ftsQuery('código año')).toBe('"código"* AND "año"*');
  });
});

describe('validateLabels', () => {
  it('normalises topics and checks values', () => {
    expect(validateLabels({ intent: 'Try', usefulness: 3, topics: ['AI Agents', 'ai agents', ''] })).toEqual({
      intent: 'try',
      usefulness: 3,
      effort: null,
      summary: null,
      action: null,
      topics: ['ai-agents'],
    });
    expect(() => validateLabels({ intent: 'maybe', usefulness: 1 })).toThrow(/intent/);
    expect(() => validateLabels({ intent: 'try', usefulness: 7 })).toThrow(/usefulness/);
    expect(() => validateLabels({ intent: 'try', usefulness: 1, effort: 'forever' })).toThrow(/effort/);
  });

  it('normalizes tags like the PC', () => {
    expect(normalizeTag('  Must Read! ')).toBe('must-read');
  });
});

describe('passwords', () => {
  it('produces the same PBKDF2 hash as Python hashlib', async () => {
    // hashlib.pbkdf2_hmac("sha256", b"correct horse battery", bytes.fromhex("00"*16), 10000).hex()
    expect(await pbkdf2Hex('correct horse battery', '00'.repeat(16), 10000)).toBe(
      '7096c5c50b592ebd65cd19315be7b465e4dcf644dcad1bf0d17902088c2a416d',
    );
  });

  it('compares in constant time', () => {
    expect(timingSafeEqual('abc', 'abc')).toBe(true);
    expect(timingSafeEqual('abc', 'abd')).toBe(false);
    expect(timingSafeEqual('abc', 'abcd')).toBe(false);
  });
});
