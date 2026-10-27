import { defaultEnabled, matchOperation } from '../src/platforms/x';

describe('matchOperation', () => {
  it('matches the GraphQL calls we capture', () => {
    expect(matchOperation('https://x.com/i/api/graphql/AbC-12_z/Bookmarks?variables=%7B%7D')).toEqual(
      { platform: 'x', operation: 'Bookmarks' },
    );
    expect(matchOperation('https://x.com/i/api/graphql/q/BookmarkFolderTimeline')).toEqual({
      platform: 'x',
      operation: 'BookmarkFolderTimeline',
    });
    expect(matchOperation('https://x.com/i/api/graphql/q/BookmarkFoldersSlice')?.operation).toBe(
      'bookmarkFoldersSlice',
    );
    expect(matchOperation('https://twitter.com/i/api/graphql/q/Likes')?.operation).toBe('Likes');
  });

  it('ignores everything else', () => {
    expect(matchOperation('https://x.com/i/api/graphql/q/HomeTimeline')).toBeNull();
    expect(matchOperation('https://x.com/i/bookmarks')).toBeNull();
    expect(matchOperation('https://evil.example/i/api/graphql/q/Bookmarks')).toBeNull();
    expect(matchOperation('https://x.com/i/api/graphql/q/BookmarksExtra')).toBeNull();
    expect(matchOperation('not a url')).toBeNull();
  });

  it('enables every operation by default', () => {
    expect(defaultEnabled()).toEqual({
      bookmarks: true,
      bookmarkfoldertimeline: true,
      bookmarkfoldersslice: true,
      likes: true,
    });
  });
});
