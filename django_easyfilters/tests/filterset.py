# -*- coding: utf-8; -*-

from datetime import datetime, date
import decimal
import operator

from django.http import QueryDict
from django.test import TestCase
from django.utils.datastructures import MultiValueDict

from django_easyfilters.filterset import FilterSet
from django_easyfilters.filters import \
    FILTER_ADD, FILTER_REMOVE, FILTER_ONLY_CHOICE, \
    ForeignKeyFilter, ValuesFilter, ChoicesFilter, ManyToManyFilter, DateTimeFilter

from models import Book, Genre, Author, BINDING_CHOICES


class TestFilterSet(TestCase):

    # Tests are written so that adding new data to fixtures won't break the
    # tests, so numbers/values are compared using DB queries. Extra care is
    # taken to ensure that there is some data that matches what we are assuming
    # is there.
    fixtures = ['django_easyfilters_tests']

    def test_queryset_no_filters(self):
        class BookFilterSet(FilterSet):
            fields = []

        qs = Book.objects.all()
        data = QueryDict('')
        f = BookFilterSet(qs, data)
        self.assertEqual(qs.count(), f.qs.count())

    def test_filterset_render(self):
        """
        Smoke test to ensure that filtersets can be rendered
        """
        class BookFilterSet(FilterSet):
            fields = [
                'genre',
                ]

        qs = Book.objects.all()
        fs = BookFilterSet(qs, QueryDict(''))
        rendered = fs.render()
        self.assertTrue('Genre' in rendered)
        self.assertEqual(rendered, unicode(fs))

        # And when in 'already filtered' mode:
        choice = fs.filters[0].get_choices(qs)[0]
        fs_filtered = BookFilterSet(qs, choice.params)
        rendered_2 = fs_filtered.render()
        self.assertTrue('Genre' in rendered_2)

    def test_get_filter_for_field(self):
        """
        Ensures that the get_filter_for_field method chooses appropriately.
        """
        class BookFilterSet(FilterSet):
            fields = [
                'genre',
                'edition',
                'binding',
                'authors',
                'date_published',
                ]

        fs = BookFilterSet(Book.objects.all(), QueryDict(''))
        self.assertEqual(ForeignKeyFilter, type(fs.filters[0]))
        self.assertEqual(ValuesFilter, type(fs.filters[1]))
        self.assertEqual(ChoicesFilter, type(fs.filters[2]))
        self.assertEqual(ManyToManyFilter, type(fs.filters[3]))
        self.assertEqual(DateTimeFilter, type(fs.filters[4]))


class TestFilters(TestCase):
    fixtures = ['django_easyfilters_tests']

    def do_invalid_query_param_test(self, make_filter, params):
        """
        Utility to test filters with invalid query paramters.

        make_filter should a callable that accepts MultiValueDict
        and returns a filter.
        """
        f = make_filter(params)
        f_empty = make_filter(MultiValueDict())
        qs = f.model.objects.all()

        # invalid param should be ignored
        qs_filtered = f.apply_filter(qs)
        self.assertEqual(list(qs_filtered),
                         list(qs))

        self.assertEqual(list(f.get_choices(qs)),
                         list(f_empty.get_choices(qs)))

    def test_foreignkey_filters_produced(self):
        """
        A ForeignKey should produce a list of the possible related objects,
        with counts.
        """
        # Make another Genre that isn't used
        new_g, created = Genre.objects.get_or_create(name='Nonsense')
        assert created

        data = MultiValueDict()
        filter_ = ForeignKeyFilter('genre', Book, data)
        qs = Book.objects.all()

        choices = [(c.label, c.count) for c in filter_.get_choices(qs)]

        reached = [False, False]
        for g in Genre.objects.all():
            count = g.book_set.count()
            if count == 0:
                reached[0] = True
                self.assertTrue((g.name, count) not in choices)
            else:
                reached[1] = True
                self.assertTrue((g.name, count) in choices)

        self.assertTrue(reached[0])
        self.assertTrue(reached[1])

    def test_foreignkey_params_produced(self):
        """
        A ForeignKey filter shoud produce params that cause the query to be
        limited by that filter.
        """
        qs = Book.objects.all()
        data = MultiValueDict()
        filter1 = ForeignKeyFilter('genre', Book, data)
        choices = filter1.get_choices(qs)

        # If we use the params from e.g. the first choice, that should produce a
        # filtered qs when fed back in (i.e. when we 'click' on that option we
        # should get a filter on it).
        reached = False
        for choice in choices:
            reached = True
            filter2 = ForeignKeyFilter('genre', Book, choice.params)
            qs_filtered = filter2.apply_filter(qs)
            self.assertEqual(len(qs_filtered), choice.count)
            for book in qs_filtered:
                self.assertEqual(unicode(book.genre), choice.label)
        self.assertTrue(reached)

    def test_foreignkey_remove_link(self):
        """
        Ensure that a ForeignKey Filter will turn into a 'remove' link when an
        item has been selected.
        """
        qs = Book.objects.all()
        data = MultiValueDict()
        filter1 = ForeignKeyFilter('genre', Book, data)
        choices = filter1.get_choices(qs)
        choice = choices[0]

        filter2 = ForeignKeyFilter('genre', Book, choice.params)
        qs_filtered = filter2.apply_filter(qs)
        choices2 = filter2.get_choices(qs_filtered)

        # Should have one item
        self.assertEqual(1, len(choices2))
        self.assertEqual(choices2[0].link_type, FILTER_REMOVE)

        # 'Clicking' should remove filtering
        filter3 = ForeignKeyFilter('genre', Book, choices2[0].params)
        qs_reverted = filter3.apply_filter(qs)
        self.assertEqual(qs, qs_reverted)

    def test_foreignkey_invalid_query(self):
        self.do_invalid_query_param_test(lambda params:
                                             ForeignKeyFilter('genre', Book, params),
                                         MultiValueDict({'genre':['xxx']}))

    def test_values_filter(self):
        """
        Tests for ValuesFilter
        """
        # We combine the tests for brevity
        filter1 = ValuesFilter('edition', Book, MultiValueDict())
        qs = Book.objects.all()
        choices = filter1.get_choices(qs)

        for choice in choices:
            count = Book.objects.filter(edition=choice.params.values()[0]).count()
            self.assertEqual(choice.count, count)

            # Check the filtering
            filter2 = ValuesFilter('edition', Book, choice.params)
            qs_filtered = filter2.apply_filter(qs)
            self.assertEqual(len(qs_filtered), choice.count)
            for book in qs_filtered:
                self.assertEqual(unicode(book.edition), choice.label)

            # Check we've got a 'remove link' on filtered.
            choices_filtered = filter2.get_choices(qs)
            self.assertEqual(1, len(choices_filtered))
            self.assertEqual(choices_filtered[0].link_type, FILTER_REMOVE)


        # Check list is full, and in right order
        self.assertEqual([unicode(v) for v in Book.objects.values_list('edition', flat=True).order_by('edition').distinct()],
                         [choice.label for choice in choices])

    def test_choices_filter(self):
        """
        Tests for ChoicesFilter
        """
        filter1 = ChoicesFilter('binding', Book, MultiValueDict())
        qs = Book.objects.all()
        choices = filter1.get_choices(qs)
        # Check:
        # - order is correct.
        # - all values present (guaranteed by fixture data)
        # - choice display value is used.

        binding_choices_db = [b[0] for b in BINDING_CHOICES]
        binding_choices_display = [b[1] for b in BINDING_CHOICES]
        self.assertEqual([c.label for c in choices], binding_choices_display)

        # Check choice db value in params
        for c in choices:
            self.assertTrue(c.params.values()[0] in binding_choices_db)

    def test_manytomany_filter(self):
        """
        Tests for ManyToManyFilter
        """
        filter1 = ManyToManyFilter('authors', Book, MultiValueDict())
        qs = Book.objects.all()

        # ManyToMany can have 'drill down', i.e. multiple levels of filtering,
        # which can be removed individually.

        # First level:
        choices = filter1.get_choices(qs)

        # Check list is full, and in right order
        self.assertEqual([unicode(v) for v in Author.objects.all()],
                         [choice.label for choice in choices])

        for choice in choices:
            # For single choice, param will be single integer:
            param = int(choice.params[filter1.query_param])

            # Check the count
            count = Book.objects.filter(authors=int(param)).count()
            self.assertEqual(choice.count, count)

            author = Author.objects.get(id=param)

            # Check the label
            self.assertEqual(unicode(author),
                             choice.label)

            # Check the filtering
            filter2 = ManyToManyFilter('authors', Book, choice.params)
            qs_filtered = filter2.apply_filter(qs)
            self.assertEqual(len(qs_filtered), choice.count)

            for book in qs_filtered:
                self.assertTrue(author in book.authors.all())

            # Check we've got a 'remove link' on filtered.
            choices_filtered = filter2.get_choices(qs)
            self.assertEqual(choices_filtered[0].link_type, FILTER_REMOVE)

    def test_manytomany_filter_multiple(self):
        qs = Book.objects.all()

        # Specific example - multiple filtering
        emily = Author.objects.get(name='Emily Brontë')
        charlotte = Author.objects.get(name='Charlotte Brontë')
        anne = Author.objects.get(name='Anne Brontë')

        # If we select 'emily' as an author:

        data =  MultiValueDict({'authors':[str(emily.pk)]})
        filter1 = ManyToManyFilter('authors', Book, data)
        qs_emily = filter1.apply_filter(qs)

        # ...we should get a qs that includes Poems and Wuthering Heights.
        self.assertTrue(qs_emily.filter(name='Poems').exists())
        self.assertTrue(qs_emily.filter(name='Wuthering Heights').exists())
        # ...and excludes Jane Eyre
        self.assertFalse(qs_emily.filter(name='Jane Eyre').exists())

        # We should get a 'choices' that includes charlotte and anne
        choices = filter1.get_choices(qs_emily)
        self.assertTrue(unicode(anne) in [c.label for c in choices if c.link_type is FILTER_ADD])
        self.assertTrue(unicode(charlotte) in [c.label for c in choices if c.link_type is FILTER_ADD])

        # ... but not emily, because that is obvious and boring
        self.assertTrue(unicode(emily) not in [c.label for c in choices if c.link_type is FILTER_ADD])
        # emily should be in 'remove' links, however.
        self.assertTrue(unicode(emily) in [c.label for c in choices if c.link_type is FILTER_REMOVE])

        # If we select again:
        data2 =  MultiValueDict({'authors': [str(emily.pk), str(anne.pk)]})
        filter2 = ManyToManyFilter('authors', Book, data2)

        qs_emily_anne = filter2.apply_filter(qs)

        # ...we should get a qs that includes Poems
        self.assertTrue(qs_emily_anne.filter(name='Poems').exists())
        # ... but not Wuthering Heights
        self.assertFalse(qs_emily_anne.filter(name='Wuthering Heights').exists())

        # The choices should contain just emily and anne to remove, and
        # charlotte should have 'link_type' FILTER_ADD. Even though it
        # is the only choice, adding the choice is not necessarily the same as
        # not adding it (could have books by Rmily and Anne, but not charlotte)
        choices = filter2.get_choices(qs_emily_anne)
        self.assertEqual([(c.label, c.link_type) for c in choices],
                         [(unicode(emily), FILTER_REMOVE),
                          (unicode(anne), FILTER_REMOVE),
                          (unicode(charlotte), FILTER_ADD)])

    def test_datetime_filter_multiple_year_choices(self):
        """
        Tests that DateTimeFilter can produce choices spanning a set of years
        (and limit to max_links)
        """
        # This does drill down, and has multiple values.
        f = DateTimeFilter('date_published', Book, MultiValueDict(), max_links=10)
        qs = Book.objects.all()

        # We have enough data that it will not show a simple list of years.
        choices = f.get_choices(qs)
        self.assertTrue(len(choices) <= 10)

    def test_datetime_filter_single_year_selected(self):
        params = MultiValueDict({'date_published':['1818']})
        f = DateTimeFilter('date_published', Book, params, max_links=10)
        qs = Book.objects.all()

        # Should get a number of books in queryset.
        qs_filtered = f.apply_filter(qs)

        self.assertEqual(list(qs_filtered),
                         list(qs.filter(date_published__year=1818)))
        # We only need 1 query if we've already told it what year to look at.
        with self.assertNumQueries(1):
            choices = f.get_choices(qs_filtered)

        self.assertTrue(len([c for c in choices if c.link_type == FILTER_ADD]) >= 2)
        self.assertEqual(len([c for c in choices if c.link_type == FILTER_REMOVE]), 1)

    def test_datetime_filter_year_range_selected(self):
        params = MultiValueDict({'date_published':['1813..1814']})
        f = DateTimeFilter('date_published', Book, params, max_links=10)
        qs = Book.objects.all()

        # Should get a number of books in queryset.
        qs_filtered = f.apply_filter(qs)

        start = date(1813, 1, 1)
        end = date(1815, 1, 1)
        self.assertEqual(list(qs_filtered),
                         list(qs.filter(date_published__gte=start,
                                        date_published__lt=end)))

        # We only need 1 query if we've already told it what years to look at,
        # and there is data for both years.
        with self.assertNumQueries(1):
            choices = f.get_choices(qs_filtered)

        self.assertEqual(len([c for c in choices if c.link_type == FILTER_REMOVE]), 1)
        self.assertEqual(len([c for c in choices if c.link_type == FILTER_ADD]), 2)
        self.assertEqual([c.label for c in choices if c.link_type == FILTER_ADD],
                         ['1813', '1814'])

    def test_datetime_filter_invalid_query(self):
        self.do_invalid_query_param_test(lambda params: DateTimeFilter('date_published', Book, params, max_links=10),
                                         MultiValueDict({'date_published':['1818xx']}))

    def test_order_by_count(self):
        """
        Tests the 'order_by_count' option.
        """
        filter1 = ForeignKeyFilter('genre', Book, MultiValueDict(), order_by_count=True)
        qs = Book.objects.all()
        choices1 = filter1.get_choices(qs)

        # Should be same after sorting by 'count'
        self.assertEqual(choices1, sorted(choices1, key=operator.attrgetter('count'), reverse=True))

        filter2 = ForeignKeyFilter('genre', Book, MultiValueDict(), order_by_count=False)
        choices2 = filter2.get_choices(qs)

        # Should be same after sorting by 'label' (that is equal to Genre.name,
        # and Genre ordering is by that field)
        self.assertEqual(choices2, sorted(choices2, key=operator.attrgetter('label')))
