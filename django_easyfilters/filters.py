from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
import math
import operator
import re

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import formats
from django.utils.datastructures import SortedDict
from django.utils.dates import MONTHS
from django.utils.text import capfirst
from django_easyfilters.queries import date_aggregation, value_counts

try:
    from collections import namedtuple
    FilterChoice = namedtuple('FilterChoice', 'label count params link_type')
except ImportError:
    # We don't use it as a tuple, so this will do:
    class FilterChoice(object):
        def __init__(self, label, count, params, link_type):
            self.label, self.count, self.params, self.link_type = label, count, params, link_type


FILTER_ADD = 'add'
FILTER_REMOVE = 'remove'
FILTER_DISPLAY = 'display'


class Filter(object):
    """
    A Filter creates links/URLs that correspond to some DB filtering,
    and can apply the information from a URL to filter a QuerySet.
    """

    ### Public interface ###

    def __init__(self, field, model, params, query_param=None, order_by_count=False):
        self.field = field
        self.model = model
        self.params = params
        if query_param is None:
            query_param = field
        self.query_param = query_param
        self.order_by_count = order_by_count
        self.field_obj = self.model._meta.get_field(self.field)

        if self.field_obj.rel is not None:
            self.rel_model = self.field_obj.rel.to
            self.rel_field = self.field_obj.rel.get_related_field()
        # Make chosen an immutable sequence, to stop accidental mutation.
        self.chosen = tuple(self.choices_from_params())

    def apply_filter(self, qs):
        """
        Apply the filtering defined in params (request.GET) to the queryset qs,
        returning the new QuerySet.
        """
        chosen = list(self.chosen)
        while len(chosen) > 0:
            lookup = self.lookup_from_choice(chosen.pop())
            qs = qs.filter(**lookup)
        return qs

    def get_choices(self, qs):
        """
        Returns a list of namedtuples containing (label (as a string), count,
        params, link type)
        """
        raise NotImplementedError()

    ### Methods that are used by base implementation above ###

    def choices_from_params(self):
        out = []
        for p in self.params.getlist(self.query_param):
            try:
                choice = self.choice_from_param(p)
                out.append(choice)
            except ValueError:
                pass
        return out

    def choice_from_param(self, param):
        """
        Returns a native Python object representing something that has been
        chosen for a filter, converted from the string value in param.
        """
        try:
            return self.field_obj.to_python(param)
        except ValidationError:
            raise ValueError()

    def lookup_from_choice(self, choice):
        """
        Converts a choice value to a lookup dictionary that can be passed to
        QuerySet.filter() to do the filtering for that choice.
        """
        return {self.field: choice}

    ### Utility methods needed by most/all subclasses ###

    def param_from_choices(self, choices):
        """
        For a list of choices, return the parameter list that should be created.
        """
        return map(unicode, choices)

    def build_params(self, add=None, remove=None):
        """
        Builds a new parameter MultiDict.
        add is an optional item to add,
        remove is an option list of items to remove.
        """
        params = self.params.copy()
        chosen = list(self.chosen)
        if remove is not None:
            for r in remove:
                chosen.remove(r)
        else:
            if add not in chosen:
                chosen.append(add)
        if chosen:
            params.setlist(self.query_param, self.param_from_choices(chosen))
        else:
            del params[self.query_param]
        params.pop('page', None) # links should reset paging
        return params

    def sort_choices(self, qs, choices):
        """
        Sorts the choices by applying order_by_count if applicable.

        See also sort_choices_custom.
        """
        if self.order_by_count:
            choices.sort(key=operator.attrgetter('count'), reverse=True)
        return choices

    def normalize_add_choices(self, choices):
        return choices

    def get_choices_remove(self, qs):
        chosen = self.chosen
        choices = []
        for choice in chosen:
            display = self.display_choice(choice)
            if display is not None:
                choices.append(FilterChoice(display,
                                            None, # Don't need count for removing
                                            self.build_params(remove=[choice]),
                                            FILTER_REMOVE))
        return choices


class SingleValueMixin(object):
    """
    A mixin for filters where the field conceptually has just one value.
    """
    def normalize_add_choices(self, choices):
        addchoices = [(i, choice) for i, choice in enumerate(choices)
                      if choice.link_type == FILTER_ADD]
        if len(addchoices) == 1 and not self.field_obj.null:
            # No point giving people a choice of one, since all the results will
            # already have the selected value (apart from nullable fields, which
            # might have null)
            for i, c in addchoices:
                choices[i] = FilterChoice(label=choices[i].label,
                                          count=choices[i].count,
                                          link_type=FILTER_DISPLAY,
                                          params=None)
        return choices


class ChooseOnceMixin(SingleValueMixin):
    """
    A mixin for filters where you can only choose the filter once, and then
    remove the filter.
    """
    def get_choices(self, qs):
        choices_remove = self.get_choices_remove(qs)
        if len(choices_remove) > 0:
            return choices_remove
        else:
            choices_add = self.normalize_add_choices(self.get_choices_add(qs))
            return self.sort_choices(qs, choices_add)

    def get_choices_add(self, qs):
        raise NotImplementedError()


class ChooseAgainMixin(object):
    """
    A mixin for filters where it is possible to choose the filter more than
    once.
    """
    # This includes drill down, as well as many-valued fields.
    def get_choices(self, qs):
        # In general, can filter multiple times, so we can have multiple remove
        # links, and multiple add links, at the same time.
        choices_remove = self.get_choices_remove(qs)
        choices_add = self.normalize_add_choices(self.get_choices_add(qs))
        choices_add = self.sort_choices(qs, choices_add)
        return choices_remove + choices_add


class RelatedObjectMixin(object):
    """
    Mixin for fields that need to validate params against related field.
    """
    def choice_from_param(self, param):
        try:
            return self.rel_field.to_python(param)
        except ValidationError:
            raise ValueError()


class SimpleQueryMixin(object):
    """
    Mixin for filters that do a simple DB query on main table to get counts.
    """
    def get_values_counts(self, qs):
        """
        Returns a SortedDict dictionary of {value: count}.

        The order is the underlying order produced by sorting ascending on the
        DB field.
        """
        return value_counts(qs, self.field)


class RangeFilterMixin(ChooseAgainMixin, SingleValueMixin):

    # choice_type must be set to a class that provides the static method
    # 'from_param' and instance methods 'make_lookup' and 'display', and the
    # __cmp__ and __eq__ methods for sorting.
    choice_type = None

    def choice_from_param(self, param):
        return self.choice_type.from_param(param)

    def choices_from_params(self):
        choices = super(RangeFilterMixin, self).choices_from_params()
        choices.sort()
        return choices

    def lookup_from_choice(self, choice):
        return choice.make_lookup(self.field)

    def display_choice(self, choice):
        return choice.display()

    def get_choices_remove(self, qs):
        # Due to drill down, if a broader param is removed, the more specific
        # params must be removed too. We assume we can do an ordering on
        # whatever 'choice' objects are in chosen, and 'greater' means 'more
        # specific'.
        chosen = list(self.chosen)
        out = []
        for i, choice in enumerate(chosen):
            to_remove = [c for c in chosen if c >= choice]
            out.append(FilterChoice(self.display_choice(choice),
                                    None,
                                    self.build_params(remove=to_remove),
                                    FILTER_REMOVE))
        return out


### Concrete filter classes that are used by FilterSet ###

class ValuesFilter(ChooseOnceMixin, SimpleQueryMixin, Filter):
    """
    Fallback Filter for various kinds of simple values.
    """
    def display_choice(self, choice):
        retval = unicode(choice)
        if retval == u'':
            return u'(empty)'
        else:
            return retval

    def get_choices_add(self, qs):
        """
        Called by 'get_choices', this is usually the one to override.
        """
        count_dict = self.get_values_counts(qs)
        return [FilterChoice(self.display_choice(val),
                             count,
                             self.build_params(add=val),
                             FILTER_ADD)
                for val, count in count_dict.items()]


class ChoicesFilter(ValuesFilter):
    """
    Filter for fields that have 'choices' defined.
    """
    # Need to do the following:
    # 1) ensure we only display options that are in 'choices'
    # 2) ensure the order is the same as in choices
    # 3) make display value = the second element in choices' tuples.
    def __init__(self, *args, **kwargs):
        super(ChoicesFilter, self).__init__(*args, **kwargs)
        self.choices_dict = dict(self.field_obj.flatchoices)

    def display_choice(self, choice):
        # 3) above
        return self.choices_dict.get(choice, choice)

    def get_choices_add(self, qs):
        count_dict = self.get_values_counts(qs)
        choices = []
        for val, display in self.field_obj.choices:
            # 1), 2) above
            if val in count_dict:
                # We could use the value 'display' here, but for consistency
                # call display_choice() in case it is overriden.
                choices.append(FilterChoice(self.display_choice(val),
                                            count_dict[val],
                                            self.build_params(add=val),
                                            FILTER_ADD))
        return choices


class ForeignKeyFilter(ChooseOnceMixin, SimpleQueryMixin, RelatedObjectMixin, Filter):
    """
    Filter for ForeignKey fields.
    """
    def display_choice(self, choice):

        lookup = {self.rel_field.name: choice}
        try:
            obj = self.rel_model.objects.get(**lookup)
        except self.rel_model.DoesNotExist:
            return None
        return unicode(obj)

    def get_choices_add(self, qs):
        count_dict = self.get_values_counts(qs)
        lookup = {self.rel_field.name + '__in': count_dict.keys()}
        objs = self.rel_model.objects.filter(**lookup)
        choices = []

        for o in objs:
            pk = getattr(o, self.rel_field.attname)
            choices.append(FilterChoice(unicode(o),
                                        count_dict[pk],
                                        self.build_params(add=pk),
                                        FILTER_ADD))
        return choices


class ManyToManyFilter(ChooseAgainMixin, RelatedObjectMixin, Filter):

    def get_values_counts(self, qs):
        # It is easiest to base queries around the intermediate table, in order
        # to get counts.
        through = self.field_obj.rel.through
        rel_model = self.rel_model

        assert rel_model != self.model, "Can't cope with this yet..."
        fkey_this = [f for f in through._meta.fields
                     if f.rel is not None and f.rel.to is self.model][0]
        fkey_other = [f for f in through._meta.fields
                      if f.rel is not None and f.rel.to is rel_model][0]

        # We need to limit items by what is in the main QuerySet (which might
        # already be filtered).
        m2m_objs = through.objects.filter(**{fkey_this.name + '__in':qs})

        # We need to exclude items in other table that we have already filtered
        # on, because they are not interesting.
        m2m_objs = m2m_objs.exclude(**{fkey_other.name + '__in': self.chosen})

        # Now get counts:
        field_name = fkey_other.name
        return value_counts(m2m_objs, field_name)

    def get_choices_add(self, qs):
        count_dict = self.get_values_counts(qs)
        # Now, need to lookup objects on related table, to display them.
        objs = self.rel_model.objects.filter(pk__in=count_dict.keys())

        return [FilterChoice(unicode(o),
                             count_dict[o.pk],
                             self.build_params(add=o.pk),
                             FILTER_ADD)
                for o in objs]

    def get_choices_remove(self, qs):
        chosen = self.chosen
        # Do a query in bulk to get objs corresponding to choices.
        objs = self.rel_model.objects.filter(pk__in=chosen)

        # We want to preserve order of items in params, so use the original
        # 'chosen' list, rather than objs.
        obj_dict = dict([(obj.pk, obj) for obj in objs])
        return [FilterChoice(unicode(obj_dict[choice]),
                             None, # Don't need count for removing
                             self.build_params(remove=[choice]),
                             FILTER_REMOVE)
                for choice in chosen if choice in obj_dict]


class DateRangeType(object):

    all = {} # Keep a cache, so that we have unique instances

    def __init__(self, level, single, label, regex):
        self.level, self.single, self.label = level, single, label
        self.regex = re.compile((r'^(%s)$' % regex) if single else
                                (r'^(%s)..(%s)$' % (regex, regex)))
        DateRangeType.all[(level, single)] = self

    def __repr__(self):
        return '<DateRange %d %s %s>' % (self.level,
                                         "single" if self.single else "multi",
                                         self.label)

    def __cmp__(self, other):
        if other is None:
            return 1
        else:
            return cmp((self.level, self.single),
                       (other.level, other.single))

    @classmethod
    def get(cls, level, single):
        return cls.all[(level, single)]

    @property
    def dateattr(self):
        # The attribute of a date object that we truncate to when collapsing results.
        return self.label

    @property
    def relativedeltaattr(self):
        # The attribute to use for calculations using relativedelta
        return self.label + 's'

    def drilldown(self):
        if self is DAY:
            return None
        if not self.single:
            return DateRangeType.get(self.level, True)
        else:
            # We always drill down to 'single', and then generate
            # ranges (i.e. multi) if appropriate.
            return DateRangeType.get(self.level + 1, True)


_y, _ym, _ymd = r'\d{4}', r'\d{4}-\d{2}', r'\d{4}-\d{2}-\d{2}'
YEARGROUP   = DateRangeType(1, False, 'year',  _y)
YEAR        = DateRangeType(1, True,  'year',  _y)
MONTHGROUP  = DateRangeType(2, False, 'month', _ym)
MONTH       = DateRangeType(2, True,  'month', _ym)
DAYGROUP    = DateRangeType(3, False, 'day',   _ymd)
DAY         = DateRangeType(3, True,  'day',   _ymd)


class DateChoice(object):
    """
    Represents a choice of date. Params are converted to this, and this is used
    to build new params and format links.

    It can represent a year, month or day choice, or a range (start, end, both
    inclusive) of any of these choice.
    """

    def __init__(self, range_type, values):
        self.range_type = range_type
        self.values = values

    def __unicode__(self):
        # This is called when converting to URL
        return '..'.join(self.values)

    def __repr__(self):
        return '<DateChoice %s %s>' % (self.range_type, self.__unicode__())

    def __cmp__(self, other):
        return cmp((self.range_type, self.values),
                   (other.range_type, other.values))

    def display(self):
        # Called for user presentable string
        if self.range_type.single:
            value = self.values[0]
            parts = value.split('-')
            if self.range_type == YEAR:
                return parts[0]
            elif self.range_type == MONTH:
                return unicode(MONTHS[int(parts[1])])
            elif self.range_type == DAY:
                return str(int(parts[-1]))
        else:
            return u'-'.join([DateChoice(DateRangeType.get(self.range_type.level, True),
                                         [val]).display()
                              for val in self.values])

    @staticmethod
    def datetime_to_value(range_type, dt):
        if range_type == YEAR:
            return '%04d' % dt.year
        elif range_type == MONTH:
            return '%04d-%02d' % (dt.year, dt.month)
        else:
            return '%04d-%02d-%02d' % (dt.year, dt.month, dt.day)

    @staticmethod
    def from_datetime(range_type, dt):
        return DateChoice(range_type, [DateChoice.datetime_to_value(range_type, dt)])

    @staticmethod
    def from_datetime_range(range_type, dt1, dt2):
        return DateChoice(DateRangeType.get(range_type.level, False),
                          [DateChoice.datetime_to_value(range_type, dt1),
                           DateChoice.datetime_to_value(range_type, dt2)])

    @staticmethod
    def from_param(param):
        for drt in DateRangeType.all.values():
            m = drt.regex.match(param)
            if m is not None:
                return DateChoice(drt, list(m.groups()))
        raise ValueError()

    def make_lookup(self, field_name):
        # It's easier to do this all using datetime comparisons than have a
        # separate path for the single year/month/day case.
        if self.range_type.single:
            start, end = self.values[0], self.values[0]
        else:
            start, end = self.values

        start_parts = map(int, start.split('-'))
        end_parts = map(int, end.split('-'))

        # Fill the parts we don't have with '1' so that e.g. 2000 becomes
        # 2000-1-1
        start_parts = start_parts + [1] * (3 - len(start_parts))
        end_parts = end_parts + [1] * (3 - len(end_parts))
        start_date = date(start_parts[0], start_parts[1], start_parts[2])
        end_date = date(end_parts[0], end_parts[1], end_parts[2])

        # Now add one year/month/day:
        end_date = end_date + relativedelta(**{self.range_type.relativedeltaattr: 1})

        return {field_name + '__gte': start_date,
                field_name + '__lt':  end_date}


class DateTimeFilter(RangeFilterMixin, Filter):

    choice_type = DateChoice

    max_depth_levels = {'year': YEAR.level,
                        'month': MONTH.level,
                        None: DAY.level + 1}

    def __init__(self, *args, **kwargs):
        self.max_links = kwargs.pop('max_links', 12)
        self.max_depth = kwargs.pop('max_depth', None)
        assert self.max_depth in ['year', 'month', None]
        self.max_depth_level = self.max_depth_levels[self.max_depth]
        super(DateTimeFilter, self).__init__(*args, **kwargs)

    def get_choices_add(self, qs):
        chosen = list(self.chosen)
        range_type = None

        if len(chosen) > 0:
            range_type = chosen[-1].range_type.drilldown()
            if range_type is None:
                return []

        if range_type is None:
            # Get some initial idea of range
            date_range = qs.aggregate(first=models.Min(self.field),
                                      last=models.Max(self.field))
            first = date_range['first']
            last = date_range['last']
            if first is None or last is None:
                # No values, can't drill down:
                return []
            if first.year == last.year:
                if first.month == last.month:
                    range_type = DAY
                else:
                    range_type = MONTH
            else:
                range_type = YEAR

        date_qs = qs.dates(self.field, range_type.label)
        results = date_aggregation(date_qs)

        date_choice_counts = self.collapse_results(results, range_type)

        choices = []
        # Additional display links, to give context for choices if necessary.
        if len(date_choice_counts) > 0:
            choices.extend(self.bridge_choices(chosen, date_choice_counts))

        for date_choice, count in date_choice_counts:
            if date_choice in chosen:
                continue

            # To ensure we get the bridge choices, which are useful, we check
            # self.max_depth_level late on and bailout here.
            if range_type.level > self.max_depth_level:
                continue

            choices.append(FilterChoice(date_choice.display(),
                                        count,
                                        self.build_params(add=date_choice),
                                        FILTER_ADD))
        return choices

    def collapse_results(self, results, range_type):
        if len(results) > self.max_links:
            # If range_type is month/day, we don't want any possibility of the
            # buckets wrapping over to the next year/month, so we set first and
            # last accordingly
            if range_type == MONTH:
                first, last = 1, 12
            elif range_type == DAY:
                first, last = 1, 31
            else:
                first = results[0][0].year
                last = results[-1][0].year

            # We need to split into even sized buckets, so it looks nice.
            span =  last - first + 1
            bucketsize = int(math.ceil(float(span) / self.max_links))
            numbuckets = int(math.ceil(float(span) / bucketsize))

            buckets = [[] for i in range(numbuckets)]
            for row in results:
                val = getattr(row[0], range_type.dateattr)
                bucketnum = int(math.floor(float(val - first)/bucketsize))
                buckets[bucketnum].append(row)

            dt_template = results[0][0]
            date_choice_counts = []
            for i, bucket in enumerate(buckets):
                count = sum(row[1] for row in bucket)
                start_val = first + bucketsize * i
                start_date = dt_template.replace(**dict({range_type.dateattr: start_val}))
                end_date = start_date + relativedelta(**dict({range_type.relativedeltaattr: bucketsize - 1}))

                choice = DateChoice.from_datetime_range(range_type, start_date, end_date)
                date_choice_counts.append((choice, count))
        else:
            date_choice_counts = [(DateChoice.from_datetime(range_type, dt), count)
                                  for dt, count in results]
        return date_choice_counts

    def bridge_choices(self, chosen, choices):
        # Returns FILTER_DISPLAY type choices to bridge from what is chosen
        # (which might be nothing) to the first 'add' link, to give context to
        # the link.
        if len(choices) == 0:
            return []
        if len(chosen) == 0:
            chosen_level = 0
        else:
            chosen_level = chosen[-1].range_type.level

        # first choice in list can act as template, as it will have all the
        # values we need.
        new_choice = choices[0][0]
        new_level = new_choice.range_type.level

        retval = []
        while chosen_level < new_level - 1:
            chosen_level += 1
            if chosen_level > self.max_depth_level:
                continue
            date_choice = DateChoice(DateRangeType.get(chosen_level, True),
                                     new_choice.values)
            retval.append(FilterChoice(date_choice.display(),
                                       None, None,
                                       FILTER_DISPLAY))
        return retval

