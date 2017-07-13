# -*- coding: utf-8 -*-

from django.contrib.postgres.fields import JSONField
from django.core.exceptions import ImproperlyConfigured
from django.db.models import fields
from django.db.models.expressions import RawSQL
from django.db.models.functions import Cast, Coalesce
from django.utils.translation import ugettext as _

from .settings import DEFAULT_LANGUAGE
from .utils import build_localized_fieldname, get_language

SUPPORTED_FIELDS = (
    fields.CharField,
    fields.TextField,
)


def translated_field_factory(original_field, language=None, *args, **kwargs):
    if not isinstance(original_field, SUPPORTED_FIELDS):
        raise ImproperlyConfigured(
            '{} is not supported by django-modeltrans.'.format(original_field.__class__.__name__)
        )

    class Specific(TranslatedVirtualField, original_field.__class__):
        pass

    Specific.__name__ = 'Translation{}'.format(original_field.__class__.__name__)

    return Specific(original_field, language, *args, **kwargs)


class TranslatedVirtualField(object):
    '''
    A field representing a single field translated to a specific language.

    Arguments:
        original_field: The original field to be translated
        language: The lanuage to translate to, or `None` to track the current
            active Django language.
    '''
    # Implementation inspired by HStoreVirtualMixin from:
    # https://github.com/djangonauts/django-hstore/blob/master/django_hstore/virtual.py

    def __init__(self, original_field, language=None, *args, **kwargs):
        # TODO: this feels like a big hack.
        self.__dict__.update(original_field.__dict__)

        self.original_field = original_field
        self.language = language

        self.blank = kwargs['blank']
        self.null = kwargs['null']
        self.editable = kwargs.get('editable', True)

        self.concrete = False

    @property
    def original_name(self):
        return self.original_field.name

    def contribute_to_class(self, cls, name):
        self.model = cls

        self.attname = name
        self.name = name
        self.column = None

        # Use a translated verbose name:
        translated_field_name = _(self.original_field.verbose_name)
        if self.language is not None:
            translated_field_name += ' ({})'.format(self.language.upper())
        self.verbose_name = translated_field_name

        setattr(cls, name, self)
        cls._meta.add_field(self, private=True)

    def db_type(self, connection):
        return None

    def __get__(self, instance, instance_type=None):
        # this method is apparantly called with instance=None from django.
        # django-hstor raises AttributeError here, but that doesn't solve
        # our problem.
        if instance is None:
            return

        language = self.get_language()
        if language == DEFAULT_LANGUAGE:
            return getattr(instance, self.original_name)

        # Make sure we test for containment in a dict, not in None
        if instance.i18n is None:
            instance.i18n = {}

        # fallback (only for <original_field>_i18n fields)
        field_name = build_localized_fieldname(self.original_name, language)
        if self.language is None and field_name not in instance.i18n:
            return getattr(instance, self.original_name)

        return instance.i18n.get(field_name)

    def __set__(self, instance, value):
        if instance.i18n is None:
            instance.i18n = {}

        language = self.get_language()

        if language == DEFAULT_LANGUAGE:
            setattr(instance, self.original_name, value)
        else:
            field_name = build_localized_fieldname(self.original_name, language)

            # if value is None, remove field from `i18n`.
            if value is None:
                instance.i18n.pop(field_name, None)
            else:
                instance.i18n[field_name] = value

    def get_field_name(self):
        '''
        Returns the field name for this virtual field.

        Two options:
            - <original_field_name>_i18n for the current active language
            - <original_field_name>_<language> for the specific translation
        '''
        if self.language is None:
            lang = 'i18n'
        else:
            lang = self.get_language()

        return build_localized_fieldname(self.original_name, lang)

    def get_language(self):
        '''
        Returns the language for this field.

        In case of an explicit language (title_en), it returns 'en', in case of
        `title_i18n`, it returns the currently active Django language.
        '''
        return self.language if self.language is not None else get_language()

    def output_field(self):
        '''
        The type of field used to Cast/Coalesce to.

        Mainly because a max_length argument is required for CharField
        until this PR is merged: https://github.com/django/django/pull/8758
        '''
        Field = self.original_field.__class__
        if isinstance(self.original_field, fields.CharField):
            return Field(max_length=self.original_field.max_length)

        return Field()

    def sql_lookup(self, fallback=True):
        '''
        Compose the sql lookup to get the value for this virtual field in a query.
        '''

        language = self.get_language()
        if language == DEFAULT_LANGUAGE:
            return self.original_name

        name = build_localized_fieldname(self.original_name, language)

        i18n_lookup = RawSQL('{}.i18n->>%s'.format(self.model._meta.db_table), (name, ))

        if fallback:
            return Coalesce(i18n_lookup, self.original_name, output_field=self.output_field())
        else:
            return Cast(i18n_lookup, self.output_field())


class TranslationField(JSONField):
    '''
    This model fields is used to store the translations in the translated model.

    Arguments:
        fields (iterable): List of column names to make translatable.
        required_languages (iterable): List of languages required for the model.
        virtual_fields (bool): If True, add virtual fields to access translated
            values with. Used during migration from django-modeltranslation to
            prevent collisions with it's database fields while having the `i18n`
            field available.
    '''
    description = 'Translation storage for a model'

    def __init__(self, fields=None, required_languages=None, virtual_fields=True, *args, **kwargs):
        self.fields = fields or ()
        self.required_languages = required_languages or ()
        self.virtual_fields = virtual_fields

        kwargs['editable'] = False
        kwargs['null'] = True
        super(TranslationField, self).__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super(TranslationField, self).deconstruct()

        del kwargs['editable']
        del kwargs['null']
        kwargs['fields'] = self.fields
        kwargs['required_languages'] = self.required_languages
        kwargs['virtual_fields'] = self.virtual_fields

        return name, path, args, kwargs

    def contribute_to_class(self, cls, name):
        if name != 'i18n':
            raise ImproperlyConfigured('{} must have name "i18n"'.format(self.__name__))

        super(TranslationField, self).contribute_to_class(cls, name)
