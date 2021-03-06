from django.http import Http404
from django.db import models
from django.core.urlresolvers import reverse
from .  import app_settings
import requests
import json
from eulxml.xmlmap import load_xmlobject_from_string
from bdrxml import mods

# Database Models
class Biography(models.Model):

    name = models.CharField(max_length=254, help_text='Enter name as it appears in the book metadata')
    trp_id = models.CharField(max_length=15, unique=True, blank=True, help_text='Optional: auto-generated by the server')
    alternate_names = models.CharField(max_length=254, null=True, blank=True, help_text='Optional: enter alternate names separated by a semi-colon')
    external_id = models.CharField(max_length=254, null=True, blank=True, help_text='Optional: enter Ulan id in the form of a URL; if there is no Ulan id, enter LCCN in the form of a URL')
    birth_date = models.CharField(max_length=25, null=True, blank=True, help_text='Optional: enter birth date as yyyy-mm-dd (for sorting and filtering)')
    death_date = models.CharField(max_length=25, null=True, blank=True, help_text='Optional: enter death date as yyyy-mm-dd')
    roles = models.CharField(max_length=254, null=True, blank=True, help_text='Optional: enter roles, separated by a semi-colon')
    bio = models.TextField()

    class Meta:
        verbose_name_plural = 'biographies'
        ordering = ['name']

    def books(self):
        return Book.search(query='name:"%s"' % self.name )

    def prints(self):
        return Print.search(query='contributor:"%s"' % self.name )

    def annotations_by_books_and_prints(self, group_amount=50):
        # Might need some cleaning up later, see if we can use objects here

        #Look up every annotation for a person
        num_prints_estimate = 6000
        query_uri = 'https://%s/api/search/?q=ir_collection_id:621+AND+object_type:"annotation"+AND+contributor:"%s"+AND+display:BDR_PUBLIC&rows=%s&fl=rel_is_annotation_of_ssim,primary_title,pid,nonsort' % (app_settings.BDR_SERVER, self.name, num_prints_estimate)
        annotations = json.loads(requests.get(query_uri).text)['response']['docs']
        pages = dict([(page['rel_is_annotation_of_ssim'][0].split(u':')[-1], page) for page in annotations])
        books = {}
        prints = []
        pages_to_look_up = []

        # create a list of pages the annotations are attached to
        for page_id in pages:
            page = pages[page_id]
            page['title'] = get_full_title_static(page)
            page['page_id'] = page_id
            page['id'] = page_id.split(u':')[-1]
            pages_to_look_up.append(pages[page_id]['rel_is_annotation_of_ssim'][0].replace(u':', u'\:'))
            page['thumb'] = u"https://%s/viewers/image/thumbnail/%s/"  % (app_settings.BDR_SERVER, page['rel_is_annotation_of_ssim'][0])

        num_pages = len(pages_to_look_up)
        i = 0
        # Look up which books those pages are part of in groups of group_amount 
        # (by default 50, any longer tends to break the request because the URL is too long)
        while(i < num_pages):
            group = pages_to_look_up[i : i+group_amount]
            pids = "(pid:" + ("+OR+pid:".join(group)) + ")"
            book_query = u"https://%s/api/search/?q=%s+AND+display:BDR_PUBLIC&fl=pid,primary_title,nonsort,object_type,rel_is_part_of_ssim,rel_has_pagination_ssim&rows=%s" % (app_settings.BDR_SERVER, pids, group_amount)
            data = json.loads(requests.get(book_query).text)
            book_response = data['response']['docs']

            # Create a dict that maps book pids to a list of pages for that book
            # essentially:
            # {
            #    "123456": {
            #                 'pid':'123456'
            #                 'title':"Sculpture in Rome"
            #                 'pages': [{...}, {...}, ...] (all pages in this book with annotations)
            #              }
            #    "456789": {
            #                  ...
            #              }
            #    ...
            # }
            # Also deals with any prints that came up in the search
            for p in book_response:
                try:
                    pid = p['rel_is_part_of_ssim'][0].split(u':')[-1]
                    n = int(p['rel_has_pagination_ssim'][0])
                    
                    if(pid not in books):
                        books[pid] = {}
                        books[pid]['title'] = get_full_title_static(p)
                        books[pid]['pages'] = dict()
                        books[pid]['pid'] = pid
                    books[pid]['pages'][n] = pages[p['pid'].split(u':')[-1]]
                except KeyError:
                    pid = p['pid'].split(u':')[-1]
                    p_obj = {}
                    p_obj['primary_title'] = get_full_title_static(p)
                    p_obj['pid'] = p['pid']
                    prints.append(Print(data=p_obj))

            i += group_amount

        for b in books:
            books[b]['pages'] = sorted(books[b]['pages'].items())

        return (books,prints)


    def _get_trp_id(self):
        last_bio = Biography.objects.order_by('-trp_id')[0]
        last_trp_id = int(last_bio.trp_id)
        new_trp_id = last_trp_id + 1
        return '%04d' % new_trp_id

    def save(self, *args, **kwargs):
        if not self.trp_id:
            self.trp_id = self._get_trp_id()
        super(Biography, self).save(*args, **kwargs)

    def __unicode__(self):
        return u'%s (%s)' % (self.name, self.trp_id)


class Essay(models.Model):

    slug = models.SlugField(max_length=254)
    author = models.CharField(max_length=254)
    title = models.CharField(max_length=254)
    text = models.TextField()
    pids = models.CharField(max_length=254, null=True, blank=True, help_text='Comma-separated list of pids for books or prints associated with this essay.')
    people = models.ManyToManyField(Biography, null=True, blank=True, help_text='List of people associated with this essay.')


class Genre(models.Model):
    text = models.CharField(max_length=50, unique=True)
    external_id = models.CharField(max_length=50, blank=True)

    def __unicode__(self):
        return unicode(self.text)


class Role(models.Model):
    text = models.CharField(max_length=50, unique=True)
    external_id = models.CharField(max_length=50, blank=True)

    def __unicode__(self):
        return unicode(self.text)


# Non-Database Models
class BDRObject(object):
    def __init__(self, data=None, parent=None):
        self.data= data or {}
        self.parent= parent

    def __nonzero__(self):
        return bool(self.data)

    def __getattr__(self, name):
        if name in self.data:
            return self.data.get(name)
        else:
            raise AttributeError

    def __eq__(self, other):
        sid = self.data.get("pid", "").split(":")[-1]
        oid = other.data.get("pid", "").split(":")[-1]
        return sid == oid

    def __contains__(self, item):
        return item in self.data

    OBJECT_TYPE = "*"
    @classmethod
    def search(cls, query="*", rows=6000):
        url1 = 'https://%s/api/collections/621/?q=%s&fq=object_type:%s&fl=*&fq=discover:BDR_PUBLIC&rows=%s' % (app_settings.BDR_SERVER, query, cls.OBJECT_TYPE, rows)
        objects_json = json.loads(requests.get(url1).text)
        num_objects = objects_json['items']['numFound']
        if num_objects>rows: #only reload if we need to find more bdr_objects
            return cls.search(query, num_objects)
        return [ cls(data=obj_data) for obj_data in objects_json['items']['docs'] ]


    @classmethod
    def get(cls, pid):
        json_uri='https://%s/api/items/%s/?q=*&fl=*' % (app_settings.BDR_SERVER, pid)
        resp = requests.get(json_uri)
        if not resp.ok:
             return cls()
        return cls(data=json.loads(resp.text))

    @classmethod
    def get_or_404(cls, pid):
        obj = cls.get(pid)
        if not obj:
            raise Http404
        return obj


    @property
    def id(self):
        return self.data.get('pid','').split(":")[-1]

    def _get_full_title(self):
        data = self.data
        if 'nonsort' not in data:
            return u'%s' % data['primary_title']
        if data['nonsort'].endswith(u"'"):
            return u'%s%s' % (data['nonsort'], data['primary_title'])
        return u'%s %s' % (data['nonsort'], data['primary_title'])

    @property
    def studio_uri(self):
        return self.uri

    def title(self):
        return self._get_full_title()

    def title_sort(self):
        return self.data['primary_title']

    def sort_key(self, sort_by):
        if(sort_by == 'title_sort'):
            return (self.title_sort(), self.date())
        elif(sort_by == 'authors'):
            return (self.authors(), self.title_sort(), self.date())
        return (self.date(), self.title_sort())

    def alt_titles(self):
        if "mods_title_alt" in self.data:
            return self.mods_title_alt
        return []

    def date(self):
        if "dateCreated" in self.data:
            return self.dateCreated[0:4]
        if "dateIssued" in self.data:
            return self.dateIssued[0:4]
        return "n.d"

    def authors(self):
        if "contributor_display" in self.data:
            return "; ".join(self.contributor_display)
        return "Anonymous"

    @property
    def thumbnail_src(self):
        return 'https://%s/viewers/image/thumbnail/%s/' % (app_settings.BDR_SERVER, self.pid)

from django.utils.datastructures import SortedDict
# Book
class Book(BDRObject):
    OBJECT_TYPE = "implicit-set"
    CUTOFF = 80
    SORT_OPTIONS = SortedDict([
        ( 'authors', 'authors' ),
        ( 'title', 'title_sort' ),
        ( 'date', 'date' ),
    ])

    @property
    def thumbnail_url(self):
        return  reverse('thumbnail_viewer', kwargs={'book_id': self.id})

    @property
    def short_title(self):
        if self.title_cut():
            return self.title()[0:Book.CUTOFF-3]+"..."
        return self.title()

    def title_cut(self):
        return bool(len(self.title()) > Book.CUTOFF)

    def port_url(self):
        return 'https://%s/viewers/readers/portfolio/%s/' % (app_settings.BDR_SERVER, self.pid)

    def book_url(self):
        return 'https://%s/viewers/readers/set/%s/' % (app_settings.BDR_SERVER, self.pid)

    def pages(self):
        return [ Page(data=page_data, parent=self) for page_data in self.relations['hasPart'] ]


# Page
class Page(BDRObject):
    SORT_OPTIONS = SortedDict([
        ( 'authors', 'authors' ),
        ( 'title', 'title' ),
        ( 'date', 'date' ),
    ])
    OBJECT_TYPE = "implicit-set" #TODO change to something more page appropriate

    def embedded_viewer_src(self):
        return 'https://%s/viewers/image/zoom/%s/' % (app_settings.BDR_SERVER, self.pid)

    def url(self):
        return reverse('book_page_viewer', args=[self.parent.id, self.id])

# Print
class Print(Page):
    OBJECT_TYPE = "image-compound"

    def url(self):
        return reverse('specific_print', args=[self.id,])

class Annotation(object):

    @classmethod
    def from_form_data(cls, image_pid, annotator, form_data, person_formset_data, inscription_formset_data, pid=None):
        return cls(image_pid=image_pid, annotator=annotator, pid=pid, form_data=form_data, person_formset_data=person_formset_data, inscription_formset_data=inscription_formset_data)

    @classmethod
    def from_pid(cls, pid):
        r = requests.get('%s%s/' % (app_settings.BDR_ANNOTATION_URL, pid))
        if not r.ok:
            raise Exception('error retrieving annotation data for %s: %s - %s' % (pid, r.status_code, r.content))
        mods_obj = load_xmlobject_from_string(r.content, mods.Mods)
        return cls(pid=pid, mods_obj=mods_obj)

    def __init__(self, image_pid=None, annotator=None, pid=None, form_data=None, person_formset_data=[], inscription_formset_data=[], mods_obj=None):
        self._image_pid = image_pid #pid of the object that we're adding the annotation for
        self._annotator = annotator
        self._form_data = form_data
        self._person_formset_data = [p for p in person_formset_data if p and p['person']]
        self._inscription_formset_data = [i for i in inscription_formset_data if i and i['text']]
        self._mods_obj = mods_obj
        self._pid = pid

    def add_form_data(self, annotator, form_data, person_formset_data, inscription_formset_data):
        #this is for adding the new form data when updating an annotation
        self._annotator = annotator
        self._form_data = form_data
        self._person_formset_data = [p for p in person_formset_data if p and p['person']]
        self._inscription_formset_data = [i for i in inscription_formset_data if i and i['text']]

    def get_form_data(self):
        if not self._form_data:
            self._form_data = {}
            if not self._mods_obj:
                raise Exception('no form data or mods obj')
            title1 = self._mods_obj.title_info_list[0]
            self._form_data['title'] = title1.title
            title1_lang = title1.node.get('lang')
            if title1_lang:
                self._form_data['title_language'] = title1_lang
            if len(self._mods_obj.title_info_list) > 1:
                self._form_data['english_title'] = self._mods_obj.title_info_list[1].title
            if self._mods_obj.genres and self._mods_obj.genres[0].text:
                genre = Genre.objects.get(text=self._mods_obj.genres[0].text)
                self._form_data['genre'] = genre.id
            if self._mods_obj.abstract:
                self._form_data['abstract'] = self._mods_obj.abstract.text
            if self._mods_obj.origin_info:
                if self._mods_obj.origin_info.other:
                    self._form_data['impression_date'] = self._mods_obj.origin_info.other[0].date
        return self._form_data

    def get_person_formset_data(self):
        if not self._person_formset_data:
            if not self._mods_obj:
                raise Exception('no person formset data or mods obj')
            self._person_formset_data = []
            for name in self._mods_obj.names:
                p = {}
                trp_id = name.node.get('{%s}href' % app_settings.XLINK_NAMESPACE)
                trp_id = '%04d' % int(trp_id)
                try:
                    person = Biography.objects.get(trp_id=trp_id)
                except:
                    raise Exception('no person with trp_id %s' % trp_id)
                p['person'] = person
                role_text = name.roles[0].text
                role = Role.objects.get(text=role_text)
                p['role'] = role
                self._person_formset_data.append(p)
        return self._person_formset_data

    def get_inscription_formset_data(self):
        if not self._inscription_formset_data:
            if not self._mods_obj:
                raise Exception('no inscription formset data or mods obj')
            self._inscription_formset_data = [{'text': note.text, 'location': note.label} for note in self._mods_obj.notes if note.type=='inscription']
        return self._inscription_formset_data

    def get_mods_obj(self, update=False):
        if self._mods_obj:
            #if we have mods already, and we're not updating, just return it
            if not update:
                return self._mods_obj
        else: #no self._mods_obj
            if update:
                raise Exception('no mods obj - can\'t update')
            self._mods_obj = mods.make_mods()
        #at this point, we want to put the form data into the mods obj (could be update or new)
        self._mods_obj.title_info_list = [] #clear out any old titles
        title = mods.TitleInfo()
        title.title = self._form_data['title']
        if self._form_data['title_language']:
            title.node.set('lang', self._form_data['title_language'])
        self._mods_obj.title_info_list.append(title)
        if self._form_data['english_title']:
            english_title = mods.TitleInfo()
            english_title.title = self._form_data['english_title']
            english_title.node.set('lang', 'en')
            self._mods_obj.title_info_list.append(english_title)
        if self._form_data['genre']:
            self._mods_obj.genres = [] #clear out any old genres
            genre = mods.Genre(text=self._form_data['genre'].text)
            genre.authority = 'aat'
            self._mods_obj.genres.append(genre)
        if self._form_data['abstract']:
            if not self._mods_obj.abstract:
                self._mods_obj.create_abstract()
            self._mods_obj.abstract.text = self._form_data['abstract'] #overwrites old abstract if present
        if self._form_data['impression_date']:
            #clear out old dateOther data, or create originInfo if needed
            if self._mods_obj.origin_info:
                self._mods_obj.origin_info.other = []
            else:
                self._mods_obj.create_origin_info()
            date_other = mods.DateOther(date=self._form_data['impression_date'])
            date_other.type = 'impression'
            self._mods_obj.origin_info.other.append(date_other)
        #clear out any old names:
        #   if this is a new object, there aren't any names anyway.
        #   if this is an update, either the new names will be put in, or they don't want any names.
        self._mods_obj.names = []
        for p in self._person_formset_data:
            name = mods.Name()
            np = mods.NamePart(text=p['person'].name)
            name.name_parts.append(np)
            role = mods.Role(text=p['role'].text)
            name.roles.append(role)
            href = '{%s}href' % app_settings.XLINK_NAMESPACE
            name.node.set(href, p['person'].trp_id)
            self._mods_obj.names.append(name)
        #clear out old notes data, preserving any annotor info
        self._mods_obj.notes = [note for note in self._mods_obj.notes if note.type == 'resp']
        for i in self._inscription_formset_data:
            note = mods.Note(text=i['text'])
            note.type = 'inscription'
            note.label = i['location']
            self._mods_obj.notes.append(note)
        annotator_note = mods.Note(text=self._annotator)
        annotator_note.type = 'resp'
        self._mods_obj.notes.append(annotator_note)
        return self._mods_obj

    def to_mods_xml(self, update=False):
        return self.get_mods_obj(update).serialize()

    def _get_params(self):
        params = {'identity': app_settings.BDR_IDENTITY, 'authorization_code': app_settings.BDR_AUTH_CODE}
        params['mods'] = json.dumps({u'xml_data': self.to_mods_xml()})
        params['ir'] = json.dumps({'parameters': {'ir_collection_id': 621}})
        params['rels'] = json.dumps({u'isAnnotationOf': self._image_pid})
        params['rights'] = json.dumps({'parameters': {'owner_id': app_settings.BDR_IDENTITY, 'additional_rights': 'BDR_PUBLIC#display'}})
        params['content_model'] = 'Annotation'
        return params

    def _get_update_params(self):
        params = {'identity': app_settings.BDR_IDENTITY, 'authorization_code': app_settings.BDR_AUTH_CODE}
        params['mods'] = json.dumps({u'xml_data': self.to_mods_xml(update=True)})
        if self._pid:
            params['pid'] = self._pid
        else:
            raise Exception('no pid for annotation update')
        return params

    def save_to_bdr(self):
        params = self._get_params()
        r = requests.post(app_settings.BDR_POST_URL, data=params)
        if r.ok:
            return {'pid': json.loads(r.text)['pid']}
        else:
            raise Exception('error posting new annotation for %s: %s - %s' % (self._image_pid, r.status_code, r.content))

    def update_in_bdr(self):
        params = self._get_update_params()
        r = requests.put(app_settings.BDR_POST_URL, data=params)
        if r.ok:
            return {'status': 'success'}
        else:
            raise Exception('error putting update to %s: %s - %s' % (self._pid, r.status_code, r.content))

# Copied this from views.py for getting titles from nonstandard queries
def get_full_title_static(data):
    if 'primary_title' not in data:
        return 'No Title'
    if 'nonsort' in data:
        if data['nonsort'].endswith(u"'"):
            return u'%s%s' % (data['nonsort'], data['primary_title'])
        else:
            return u'%s %s' % (data['nonsort'], data['primary_title'])
    else:
        return u'%s' % data['primary_title']
