import { render, screen } from '@testing-library/react';
import { ExpandedDetailsRow } from '@/components/elements/TruncatedDetails';

// A row opened from a table that is scrolled sideways.
function renderInScroller(overflowX) {
  return render(
    <div style={{ overflowX }}>
      <table>
        <tbody>
          <ExpandedDetailsRow text="Cancellation requested" colSpan={12} />
        </tbody>
      </table>
    </div>
  );
}

const panel = () =>
  screen.getByText('Full Details').closest('.border-gray-200');

describe('ExpandedDetailsRow', () => {
  beforeEach(() => {
    jest
      .spyOn(HTMLElement.prototype, 'clientWidth', 'get')
      .mockReturnValue(800);
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('pins the panel to the visible part of a scrolling table', () => {
    renderInScroller('auto');
    expect(panel()).toHaveStyle({
      position: 'sticky',
      left: '16px',
      width: '768px',
    });
  });

  it('leaves the panel in place when nothing scrolls sideways', () => {
    renderInScroller('visible');
    expect(panel()).not.toHaveStyle({ position: 'sticky' });
  });
});
