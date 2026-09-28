/**
 * Contract tests for the shared filter widgets.
 *
 * These exist because a page removed the `updateURLParams` prop while the
 * shared components still called it unconditionally: every add/remove/clear
 * threw `TypeError: updateURLParams is not a function` and took the page down
 * with it. Lint and `next build` cannot see that -- a missing prop is a
 * runtime fact -- so the guard has to be a rendered interaction.
 */
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import { FilterDropdown, Filters } from '@/components/shared/FilterSystem';

const PROPERTIES = [
  { label: 'Name', value: 'name' },
  { label: 'User', value: 'user' },
];

const typeAndEnter = (text) => {
  const input = screen.getByPlaceholderText('Filter items');
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: 'Enter' });
};

describe('FilterDropdown without an updateURLParams prop', () => {
  it('adds a filter instead of throwing', () => {
    let filters = [];
    const setFilters = (updater) => {
      filters = typeof updater === 'function' ? updater(filters) : updater;
    };

    render(
      <FilterDropdown
        propertyList={PROPERTIES}
        valueList={{ name: [], user: [] }}
        setFilters={setFilters}
      />
    );

    expect(() => typeAndEnter('alice')).not.toThrow();
    expect(filters).toEqual([
      { property: 'Name', operator: ':', value: 'alice' },
    ]);
  });

  it('routes the addition through a page-supplied addFilter', () => {
    // A page whose URL carries one value per property replaces rather than
    // stacks, so the chips can never say more than the address bar does.
    const addFilter = (prev, property, value) => [
      ...prev.filter((f) => f.property !== property),
      { property, operator: ':', value },
    ];
    let filters = [{ property: 'Name', operator: ':', value: 'alice' }];
    const setFilters = (updater) => {
      filters = typeof updater === 'function' ? updater(filters) : updater;
    };

    render(
      <FilterDropdown
        propertyList={PROPERTIES}
        valueList={{ name: [], user: [] }}
        setFilters={setFilters}
        addFilter={addFilter}
      />
    );

    typeAndEnter('bob');
    expect(filters).toEqual([
      { property: 'Name', operator: ':', value: 'bob' },
    ]);
  });

  it('still calls updateURLParams when a page passes one', () => {
    const updateURLParams = jest.fn();
    render(
      <FilterDropdown
        propertyList={PROPERTIES}
        valueList={{ name: [], user: [] }}
        setFilters={(u) => (typeof u === 'function' ? u([]) : u)}
        updateURLParams={updateURLParams}
      />
    );

    typeAndEnter('alice');
    expect(updateURLParams).toHaveBeenCalledWith([
      { property: 'Name', operator: ':', value: 'alice' },
    ]);
  });
});

describe('Filters chip bar without an updateURLParams prop', () => {
  const chips = [
    { property: 'Name', operator: ':', value: 'alice' },
    { property: 'User', operator: ':', value: 'bob' },
  ];

  it('removes one chip instead of throwing', () => {
    let filters = chips;
    const setFilters = (updater) => {
      filters = typeof updater === 'function' ? updater(filters) : updater;
    };

    render(<Filters filters={chips} setFilters={setFilters} />);
    const [firstRemove] = screen.getAllByTitle('Clear filter');
    expect(() => fireEvent.click(firstRemove)).not.toThrow();
    expect(filters).toEqual([chips[1]]);
  });

  it('clears every chip instead of throwing', () => {
    let filters = chips;
    const setFilters = (updater) => {
      filters = typeof updater === 'function' ? updater(filters) : updater;
    };

    render(<Filters filters={chips} setFilters={setFilters} />);
    expect(() =>
      fireEvent.click(screen.getByText('Clear filters'))
    ).not.toThrow();
    expect(filters).toEqual([]);
  });
});
