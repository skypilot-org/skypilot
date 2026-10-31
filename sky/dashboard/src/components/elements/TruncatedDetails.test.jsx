import { act, render, screen } from '@testing-library/react';
import { Table, TableBody } from '@/components/ui/table';
import { ExpandedDetailsRow } from '@/components/elements/TruncatedDetails';

// Tailwind does not load under jsdom; Table's wrapper relies on this rule.
function addTableWrapperStyle() {
  const style = document.createElement('style');
  style.textContent = '.overflow-auto { overflow-x: auto; }';
  document.head.appendChild(style);
  return () => style.remove();
}

function renderInTable() {
  return render(
    <Table>
      <TableBody>
        <ExpandedDetailsRow text="Cancellation requested" colSpan={12} />
      </TableBody>
    </Table>
  );
}

const panel = () =>
  screen.getByText('Full Details').closest('.border-gray-200');

describe('ExpandedDetailsRow', () => {
  let removeStyle;
  let clientWidth;

  beforeEach(() => {
    removeStyle = addTableWrapperStyle();
    clientWidth = jest
      .spyOn(HTMLElement.prototype, 'clientWidth', 'get')
      .mockReturnValue(800);
  });

  afterEach(() => {
    removeStyle();
    jest.restoreAllMocks();
  });

  it('pins the panel to the visible part of the table', () => {
    renderInTable();
    expect(panel()).toHaveStyle({
      position: 'sticky',
      left: '16px',
      width: '768px',
    });
  });

  it('follows the table when its visible width changes', () => {
    renderInTable();
    clientWidth.mockReturnValue(600);
    act(() => {
      window.dispatchEvent(new Event('resize'));
    });
    expect(panel()).toHaveStyle({ width: '568px' });
  });
});
