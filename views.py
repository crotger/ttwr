# -*- coding: utf-8 -*-

from django.http import HttpResponse, HttpResponseNotFound, HttpResponseServerError, HttpResponseRedirect
from django.forms.formsets import formset_factory
from django.template import Context, loader, RequestContext
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.core.urlresolvers import reverse, reverse_lazy
from django.shortcuts import render
from django.template.response import SimpleTemplateResponse
from django.utils.html import escape, escapejs
from django.contrib.auth.decorators import login_required

import json
from operator import itemgetter, methodcaller
import xml.etree.ElementTree as ET
import re
import requests
from .models import Biography, Essay, Book, Annotation
from .app_settings import BDR_SERVER, BOOKS_PER_PAGE, PID_PREFIX, logger


def std_context(style="rome/css/prints.css",title="The Theater that was Rome"):
    context={}
    context['usr_style']=style
    context['title']=title
    context['cpydate']=2013
    context['home_image']="rome/images/home.gif"
    context['brown_image']="rome/images/brown-logo.gif"
    context['stg_image']="rome/images/stg-logo.gif"
    return context


def index(request):
    template=loader.get_template('rome_templates/index.html')
    context=std_context(style="rome/css/home.css")
    c=RequestContext(request,context)
    return HttpResponse(template.render(c))


def book_list(request):
    book_list = Book.search()

    sort_by = request.GET.get('sort_by', 'authors')
    sort_by = Book.SORT_OPTIONS.get(sort_by, 'authors')

    book_list=sorted(book_list,key=methodcaller(sort_by))

    page = request.GET.get('page', 1)
    PAGIN=Paginator(book_list, BOOKS_PER_PAGE);

    return render(request,
                  'rome_templates/book_list.html',
                  {
                      'books': PAGIN.page(page),
                      'sorting': sort_by,
                      'sort_options': Book.SORT_OPTIONS,
                  }
                 )


def book_detail(request, book_id):
    book_list_page = request.GET.get('book_list_page', 1)
    return render(request,
                  'rome_templates/book_detail.html',
                  {
                    'back_to_book_href': u'%s?page=%s' % (reverse('books'), book_list_page),
                    'book': Book.get_or_404(pid="%s:%s" % (PID_PREFIX, book_id)),
                  }
                 )


def page_detail(request, page_id, book_id=None):
    page_pid = u'%s:%s' % (PID_PREFIX, page_id)
    template=loader.get_template('rome_templates/page_detail.html')
    context=std_context()
    if book_id:
        book_pid = '%s:%s' % (PID_PREFIX, book_id)
    else:
        book_pid = _get_book_pid_from_page_pid(u'%s' % page_pid)
        book_id = book_pid.split(':')[-1]
    if not book_id:
        return HttpResponseNotFound('Book for this page not found.')

    context['user'] = request.user
    if request.user.is_authenticated():
        context['create_annotation_link'] = reverse('new_annotation', kwargs={'book_id':book_id, 'page_id':page_id})

    book_list_page = request.GET.get('book_list_page', None)

    context['book_mode']=1
    context['print_mode']=0

    if book_list_page:
        context['back_to_book_href'] = u'%s?page=%s' % (reverse('books'), book_list_page)
        context['back_to_thumbnail_href'] = u'%s?book_list_page=%s' % (reverse('thumbnail_viewer', kwargs={'book_id':book_id}), book_list_page)
    else:
        context['back_to_book_href'] = reverse('books')
        context['back_to_thumbnail_href'] = reverse('thumbnail_viewer', kwargs={'book_id':book_id})

    context['studio_url'] = 'https://%s/studio/item/%s/' % (BDR_SERVER, page_pid)
    context['book_id'] = book_id
    thumbnails=[]
    book_json_uri = u'https://%s/api/pub/items/%s/' % (BDR_SERVER, book_pid)
    r = requests.get(book_json_uri, timeout=60)
    if not r.ok:
        logger.error(u'TTWR - error retrieving url %s' % book_json_uri)
        logger.error(u'TTWR - response: %s - %s' % (r.status_code, r.text))
        return HttpResponseServerError('Error retrieving content.')
    book_json = json.loads(r.text)
    context['short_title']=book_json['brief']['title']
    context['title'] = _get_full_title(book_json)
    try:
        author_list=book_json['contributor_display']
        authors=""
        for i in range(len(author_list)):
            if i==len(author_list)-1:
                authors+=author_list[i]
            else:
                authors+=author_list[i]+"; "
        context['authors']=authors
    except:
        context['authors']="contributor(s) not available"
    try:
        context['date']=book_json['dateIssued'][0:4]
    except:
        try:
            context['date']=book_json['dateCreated'][0:4]
        except:
            context['date']="n.d."
    context['lowres_url']="https://%s/fedora/objects/%s/datastreams/lowres/content" % (BDR_SERVER, page_pid)
    context['det_img_view_src']="https://%s/viewers/image/zoom/%s" % (BDR_SERVER, page_pid)

    # annotations/metadata
    page_json_uri = u'https://%s/api/pub/items/%s/' % (BDR_SERVER, page_pid)
    r = requests.get(page_json_uri, timeout=60)
    if not r.ok:
        logger.error(u'TTWR - error retrieving url %s' % page_json_uri)
        logger.error(u'TTWR - response: %s - %s' % (r.status_code, r.text))
        return HttpResponseServerError('Error retrieving content.')
    page_json = json.loads(r.text)
    annotations=page_json['relations']['hasAnnotation']
    context['has_annotations']=len(annotations)
    context['annotation_uris']=[]
    context['annotations']=[]
    for annotation in annotations:
        anno_id = annotation['pid'].split(':')[-1]
        if request.user.is_authenticated():
            link = reverse('edit_annotation', kwargs={'book_id': book_id, 'page_id': page_id, 'anno_id': anno_id})
            annotation['edit_link'] = link
        annot_xml_uri='https://%s/services/getMods/%s/' % (BDR_SERVER, annotation['pid'])
        context['annotation_uris'].append(annot_xml_uri)
        annotation['xml_uri'] = annot_xml_uri
        curr_annot = get_annotation_detail(annotation)
        context['annotations'].append(curr_annot)

    # Previous/next page links
    pagenum = int(page_json['rel_has_pagination_ssim'][0])
    context['pagenum'] = pagenum

    # Initialize both to none
    prev_pid = "none"
    next_pid = "none"

    # If the page numbers don't match the list indices, search for the correct page
    if(book_json['relations']['hasPart'][pagenum - 1]['pid'] != page_pid):
        for page in book_json['relations']['hasPart']:
            if(int(page['rel_has_pagination_ssim']) == (pagenum-1)):
                prev_pid = page['pid'].split(':')[-1]
            if(int(page['rel_has_pagination_ssim']) == (pagenum+1)):
                next_pid = page['pid'].split(':')[-1]
    else:
        
        if(pagenum != 1):
            prev_pid = book_json['relations']['hasPart'][pagenum - 2]['pid'].split(":")[-1]
        try:
            next_pid = book_json['relations']['hasPart'][pagenum]['pid'].split(":")[-1]
        except IndexError:
            next_pid = "none"

    context['prev_pid'] = prev_pid
    context['next_pid'] = next_pid

    c=RequestContext(request,context)
    return HttpResponse(template.render(c))


def get_annotation_detail(annotation):
    curr_annot={}
    curr_annot['xml_uri'] = annotation['xml_uri']
    if 'edit_link' in annotation:
        curr_annot['edit_link'] = annotation['edit_link']
    curr_annot['has_elements'] = {'inscriptions':0, 'annotations':0, 'annotator':0, 'origin':0, 'title':0, 'abstract':0, 'genre':0}

    root = ET.fromstring(requests.get(curr_annot['xml_uri']).content)
    for title in root.getiterator('{http://www.loc.gov/mods/v3}titleInfo'):
        try:
            if title.attrib['lang']=='en':
                curr_annot['title']=title[0].text
            else:
                curr_annot['orig_title']=title[0].text
        except KeyError:
            curr_annot['orig_title'] = title[0].text
        curr_annot['has_elements']['title'] += 1

    curr_annot['names']=[]
    for name in root.getiterator('{http://www.loc.gov/mods/v3}name'):
        curr_annot['names'].append({
            'name':name[0].text,
            'role':name[1][0].text.capitalize() if(name[1][0].text) else "Contributor",
            'trp_id': "%04d" % int(name.attrib['{http://www.w3.org/1999/xlink}href']),
        })
    for abstract in root.getiterator('{http://www.loc.gov/mods/v3}abstract'):
        curr_annot['abstract']=abstract.text
        curr_annot['has_elements']['abstract']=1
    for genre in root.getiterator('{http://www.loc.gov/mods/v3}genre'):
        curr_annot['genre'] = genre.text
        curr_annot['has_elements']['genre']=1
    for origin in root.getiterator('{http://www.loc.gov/mods/v3}originInfo'):
        try:
            curr_annot['origin']=origin[0].date
            curr_annot['has_elements']['origin']=1
        except:
            pass
    curr_annot['inscriptions'] = []
    curr_annot['annotations'] = []
    curr_annot['annotator'] = ""
    for note in root.getiterator('{http://www.loc.gov/mods/v3}note'):
        curr_note={}
        for att in note.attrib:
            curr_note[att]=note.attrib[att]
        if note.text:
            curr_note['text']=note.text
        if curr_note['type'].lower()=='inscription' and note.text:
            curr_annot['inscriptions'].append(curr_note['displayLabel']+": "+curr_note['text'])
            curr_annot['has_elements']['inscriptions']=1
        elif curr_note['type'].lower()=='annotation' and note.text:
            curr_annot['annotations'].append(curr_note['displayLabel']+": "+curr_note['text'])
            curr_annot['has_elements']['annotations']=1
        elif curr_note['type'].lower()=='resp' and note.text:
            #display for the first annotator; ignore later annotators for now
            if not curr_annot['annotator']:
                curr_annot['annotator'] = note.text
                curr_annot['has_elements']['annotator'] = 1
    return curr_annot


def print_list(request):
    template=loader.get_template('rome_templates/print_list.html')
    page = request.GET.get('page', 1)
    sort_by = request.GET.get('sort_by', 'authors')
    collection = request.GET.get('collection', 'both')
    chinea = ""
    if(collection == 'chinea'):
        chinea = "+AND+(primary_title:\"Chinea\"+OR+subtitle:\"Chinea\")"
    elif(collection == 'not'):
        chinea = "+NOT+primary_title:\"Chinea\"+NOT+subtitle:\"Chinea\""

    context=std_context(title="The Theater that was Rome - Prints")
    context['page_documentation']='Browse the prints in the Theater that was Rome collection. Click on "View" to explore a print further.'
    context['curr_page']=page
    context['sorting']='authors'
    if sort_by!='authors':
        context['sorting']=sort_by
    # load json for all prints in the collection #
    num_prints_estimate = 6000

    url1 = 'https://%s/api/pub/search/?q=ir_collection_id:621+AND+object_type:image-compound%s&rows=%s' % (BDR_SERVER, chinea, num_prints_estimate)
    prints_json = json.loads(requests.get(url1).text)
    num_prints = prints_json['response']['numFound']
    context['num_prints'] = num_prints
    prints_set = prints_json['response']['docs']

    print_list=[]
    for i in range(len(prints_set)): #create list of prints to load
        current_print={}
        Print=prints_set[i]
        title = _get_full_title(Print)
        current_print['in_chinea']=0
        if collection == "chinea":
            current_print['in_chinea']=1
        elif (re.search(r"chinea",title,re.IGNORECASE) or (re.search(r"chinea",Print[u'subtitle'][0],re.IGNORECASE) if u'subtitle' in Print else False)):
            current_print['in_chinea']=1
        pid=Print['pid']
        current_print['studio_uri']= 'https://%s/studio/item/%s/' % (BDR_SERVER, pid)
        short_title=title
        current_print['title_cut']=0
        current_print['thumbnail_url'] = reverse('specific_print', args=[pid.split(":")[1]])
        if len(title)>60:
            short_title=title[0:57]+"..."
            current_print['title_cut']=1
        current_print['title']=title
        current_print['short_title']=short_title
        current_print['det_img_viewer']='https://%s/viewers/image/zoom/%s' % (BDR_SERVER, pid)
        try:
            current_print['date']=Print['dateCreated'][0:4]
        except:
            try:
                current_print['date']=Print['dateIssued'][0:4]
            except:
                current_print['date']="n.d."
        try:
            author_list=Print['contributor_display']
        except KeyError:
            try:
                author_list=Print['contributor']
            except:
                author_list=["Unknown"];
        authors=""
        for i in range(len(author_list)):
            if i==len(author_list)-1:
                authors+=author_list[i]
            else:
                authors+=author_list[i]+"; "
        current_print['authors']=authors
        current_print['id']=pid.split(":")[1]
        print_list.append(current_print)


    print_list=sorted(print_list,key=itemgetter(sort_by,'authors','title','date'))
    for i, Print in enumerate(print_list):
        Print['number_in_list']=i+1
    context['print_list']=print_list

    prints_per_page=20
    context['prints_per_page']=prints_per_page
    PAGIN=Paginator(print_list,prints_per_page) #20 prints per page
    context['num_pages']=PAGIN.num_pages
    context['page_range']=PAGIN.page_range
    context['PAGIN']=PAGIN
    page_list=[]
    for i in PAGIN.page_range:
        page_list.append(PAGIN.page(i).object_list)
    context['page_list']=page_list
    context['collection']=collection

    c=RequestContext(request,context)
    return HttpResponse(template.render(c))


def print_detail(request, print_id):
    print_pid = '%s:%s' % (PID_PREFIX, print_id)
    template = loader.get_template('rome_templates/page_detail.html')
    context = std_context()

    if request.user.is_authenticated():
        context['create_annotation_link'] = reverse('new_print_annotation', kwargs={'print_id':print_id})

    prints_list_page = request.GET.get('prints_list_page', None)
    collection = request.GET.get('collection', None)

    context['book_mode'] = 0
    context['print_mode'] = 1
    context['det_img_view_src'] = 'https://%s/viewers/image/zoom/%s/' % (BDR_SERVER, print_pid)
    if prints_list_page:
        context['back_to_print_href'] = u'%s?page=%s&collection=%s' % (reverse('prints'), prints_list_page, collection)
    else:
        context['back_to_print_href'] = reverse('prints')

    context['print_id'] = print_id
    context['studio_url'] = 'https://%s/studio/item/%s/' % (BDR_SERVER, print_pid)

    json_uri = 'https://%s/api/pub/items/%s/' % (BDR_SERVER, print_pid)
    print_json = json.loads(requests.get(json_uri).text)
    context['short_title'] = print_json['brief']['title']
    context['title'] = _get_full_title(print_json)
    try:
        author_list=print_json['contributor_display']
        authors=""
        for i in range(len(author_list)):
            if i==len(author_list)-1:
                authors+=author_list[i]
            else:
                authors+=author_list[i]+"; "
        context['authors']=authors
    except:
        context['authors']="contributor(s) not available"
    try:
        context['date']=print_json['dateIssued'][0:4]
    except:
        try:
            context['date']=print_json['dateCreated'][0:4]
        except:
            context['date']="n.d."

    # annotations/metadata
    annotations=print_json['relations']['hasAnnotation']
    context['has_annotations']=len(annotations)
    context['annotation_uris']=[]
    context['annotations']=[]
    for annotation in annotations:
        annot_xml_uri='https://%s/services/getMods/%s/' % (BDR_SERVER, annotation['pid'])
        context['annotation_uris'].append(annot_xml_uri)
        annotation['xml_uri'] = annot_xml_uri
        anno_id = annotation['pid'].split(':')[-1]
        if request.user.is_authenticated():
            link = reverse('edit_print_annotation', kwargs={'print_id': print_id, 'anno_id': anno_id})
            annotation['edit_link'] = link
        curr_annot = get_annotation_detail(annotation)
        context['annotations'].append(curr_annot)

    c=RequestContext(request,context)
    #raise 404 if a certain print does not exist
    return HttpResponse(template.render(c))

def biography_detail(request, trp_id):
    #view that pull bio information from the db, instead of the BDR
    trp_id = "%04d" % int(trp_id)
    try:
        bio = Biography.objects.get(trp_id=trp_id)
    except ObjectDoesNotExist:
        return HttpResponseNotFound('Person %s Not Found' % trp_id)
    context = std_context(title="The Theater that was Rome - Biography")
    context = RequestContext(request, context)
    template = loader.get_template('rome_templates/biography_detail.html')
    context['bio'] = bio
    context['trp_id'] = trp_id
    context['books'] = bio.books()
    prints_search = bio.prints()

    # Pages related to the person by annotation
    (pages_books, prints_mentioned) = bio.annotations_by_books_and_prints()
    context['pages_books'] = pages_books
    # merge the two lists of prints
    prints_merged = [x for x in prints_mentioned if x not in prints_search]
    prints_merged[len(prints_merged):] = prints_search

    context['prints'] = prints_merged
    return HttpResponse(template.render(context))

def person_detail_tei(request, trp_id):
    pid, name = _get_info_from_trp_id(trp_id)
    if not pid:
        return HttpResponseNotFound('Not Found')
    r = requests.get(u'https://%s/fedora/objects/%s/datastreams/TEI/content' % (BDR_SERVER, pid))
    if r.ok:
        return HttpResponse(r.text)
    else:
        return HttpResponseServerError('Internal Server error')


def _get_info_from_trp_id(trp_id):
    trp_id = u'trp-%04d' % int(trp_id)
    r = requests.get(u'http://%s/api/pub/search?q=mods_id_trp_ssim:%s+AND+display:BDR_PUBLIC&fl=pid,name' % (BDR_SERVER, trp_id))
    if r.ok:
        data = json.loads(r.text)
        if data['response']['numFound'] > 0:
            return (data['response']['docs'][0]['pid'], data['response']['docs'][0]['name'])
    return None, None


def _pages_for_person(name, group_amount=50):
    # print >>sys.stderr, ("Retrieving pages for person %s" % name)
    num_prints_estimate = 6000
    query_uri = 'https://%s/api/pub/search/?q=ir_collection_id:621+AND+object_type:"annotation"+AND+contributor:"%s"+AND+display:BDR_PUBLIC&rows=%s&fl=rel_is_annotation_of_ssim,primary_title,pid,nonsort' % (BDR_SERVER, name[0], num_prints_estimate)
    pages_json = json.loads(requests.get(query_uri).text)
    pages = dict([(page['rel_is_annotation_of_ssim'][0].replace(u'bdr:', u''), page) for page in pages_json['response']['docs']])
    books = {}
    prints = []
    pages_to_look_up = []
    for page_id in pages:
        page = pages[page_id]
        page['title'] = _get_full_title(page)
        page['page_id'] = page_id
        pages_to_look_up.append(page['rel_is_annotation_of_ssim'][0].replace(u':', u'\:'))
        page['thumb'] = u"https://%s/viewers/image/thumbnail/%s/"  % (BDR_SERVER, page['rel_is_annotation_of_ssim'][0])

    num_pages = len(pages_to_look_up)
    i = 0
    while(i < num_pages):
        group = pages_to_look_up[i : i+group_amount]
        pids = "(pid:" + ("+OR+pid:".join(group)) + ")"
        book_query = u"https://%s/api/pub/search/?q=%s+AND+display:BDR_PUBLIC&fl=pid,primary_title,nonsort,object_type,rel_is_part_of_ssim,rel_has_pagination_ssim&rows=%s" % (BDR_SERVER, pids, group_amount)
        data = json.loads(requests.get(book_query).text)
        book_response = data['response']['docs']
        for p in book_response:
            if(p['object_type'] == "image-compound"):
                pid = p['pid'].replace(u'bdr:',u'')
                p_obj = {}
                p_obj['title'] = _get_full_title(p)
                p_obj['pid'] = p['pid']
                prints.append(p_obj)
            else:
                pid = p['rel_is_part_of_ssim'][0].replace(u'bdr:', u'')
                n = p['rel_has_pagination_ssim'][0]
                if(pid not in books):
                    books[pid] = {}
                    books[pid]['title'] = _get_full_title(p)
                    books[pid]['pages'] = {}
                    books[pid]['pid'] = pid
                books[pid]['pages'][int(n)] = pages[p['pid'].replace(u'bdr:', u'')]

        i += group_amount

    return books,prints

def _get_full_title(data):
    if 'primary_title' not in data:
        return 'No Title'
    if 'nonsort' in data:
        if data['nonsort'].endswith(u"'"):
            return u'%s%s' % (data['nonsort'], data['primary_title'])
        else:
            return u'%s %s' % (data['nonsort'], data['primary_title'])
    else:
        return u'%s' % data['primary_title']


def _get_book_pid_from_page_pid(page_pid):
    query = u'https://%s/api/pub/items/%s/' % (BDR_SERVER, page_pid)
    r = requests.get(query)
    if r.ok:
        data = json.loads(r.text)
        if data['relations']['isPartOf']:
            return data['relations']['isPartOf'][0]['pid']
        elif data['relations']['isMemberOf']:
            return data['relations']['isMemberOf'][0]['pid']
        else:
            return None

def filter_bios(fq, bio_list):
    return [b for b in bio_list if (b.roles and fq in b.roles)]

def biography_list(request):
    template = loader.get_template('rome_templates/biography_list.html')
    fq = request.GET.get('filter', 'all')

    bio_list = Biography.objects.all()
    role_set = set()

    for bio in bio_list:
        if bio.roles:
            bio.roles = [role.strip(" ") for role in bio.roles.split(';') if role.strip(" ") != '']
            role_set |= set(bio.roles)

    if fq != 'all':
        bio_list = filter_bios(fq, bio_list)

    bios_per_page=20
    PAGIN=Paginator(bio_list,bios_per_page)
    page_list=[]

    for i in PAGIN.page_range:
        page_list.append(PAGIN.page(i).object_list)

    context=std_context(title="The Theater that was Rome - Biographies")
    context['page_documentation']='Browse the biographies of artists related to the Theater that was Rome collection.'
    context['num_bios']=len(bio_list)
    context['bio_list']=bio_list
    context['bios_per_page']=bios_per_page
    context['num_pages']=PAGIN.num_pages
    context['page_range']=PAGIN.page_range
    context['curr_page']=1
    context['PAGIN']=PAGIN
    context['page_list']=page_list
    context['role_set']=role_set
    context['sorting'] = fq

    c=RequestContext(request, context)
    return HttpResponse(template.render(c))


def about(request):
    template=loader.get_template('rome_templates/about.html')
    context=std_context(style="rome/css/links.css")
    c=RequestContext(request,context)
    return HttpResponse(template.render(c))

def links(request):
    template=loader.get_template('rome_templates/links.html')
    context=std_context(style="rome/css/links.css")

    c=RequestContext(request,context)
    return HttpResponse(template.render(c))

def essay_list(request):
    template=loader.get_template('rome_templates/essay_list.html')
    context=std_context(style="rome/css/links.css")
    context['page_documentation']='Listed below are essays on topics that relate to the Theater that was Rome collection of books and engravings. The majority of the essays were written by students in Brown University classes that used this material, and edited by Prof. Evelyn Lincoln.'
    c=RequestContext(request,context)
    essay_objs = Essay.objects.all()
    c['essay_objs'] = essay_objs
    return HttpResponse(template.render(c))


def essay_detail(request, essay_slug):
    try:
        essay = Essay.objects.get(slug=essay_slug)
    except ObjectDoesNotExist:
        return HttpResponseNotFound('Essay %s Not Found' % essay_slug)
    template=loader.get_template('rome_templates/essay_detail.html')
    context=std_context(style="rome/css/links.css")
    context['usr_essays_style']="rome/css/essays.css"
    context['essay_text'] = essay.text
    c=RequestContext(request,context)
    return HttpResponse(template.render(c))

@login_required(login_url=reverse_lazy('rome_login'))
def new_annotation(request, book_id, page_id):
    page_pid = '%s:%s' % (PID_PREFIX, page_id)
    from .forms import AnnotationForm, PersonForm, InscriptionForm
    PersonFormSet = formset_factory(PersonForm)
    InscriptionFormSet = formset_factory(InscriptionForm)
    if request.method == 'POST':
        form = AnnotationForm(request.POST)
        person_formset = PersonFormSet(request.POST, prefix='people')
        inscription_formset = InscriptionFormSet(request.POST, prefix='inscriptions')
        if form.is_valid() and person_formset.is_valid() and inscription_formset.is_valid():
            if request.user.first_name:
                annotator = u'%s %s' % (request.user.first_name, request.user.last_name)
            else:
                annotator = u'%s' % request.user.username
            annotation = Annotation.from_form_data(page_pid, annotator, form.cleaned_data, person_formset.cleaned_data, inscription_formset.cleaned_data)
            try:
                response = annotation.save_to_bdr()
                logger.info('%s added annotation %s for %s' % (request.user.username, response['pid'], page_id))
                return HttpResponseRedirect(reverse('book_page_viewer', kwargs={'book_id': book_id, 'page_id': page_id}))
            except Exception as e:
                logger.error('%s' % e)
                return HttpResponseServerError('Internal server error. Check log.')
    else:
        inscription_formset = InscriptionFormSet(prefix='inscriptions')
        person_formset = PersonFormSet(prefix='people')
        form = AnnotationForm()

    image_link = 'https://%s/viewers/image/zoom/%s' % (BDR_SERVER, page_pid)
    return render(request, 'rome_templates/new_annotation.html',
            {'form': form, 'person_formset': person_formset, 'inscription_formset': inscription_formset, 'image_link': image_link})


@login_required(login_url=reverse_lazy('rome_login'))
def new_print_annotation(request, print_id):
    print_pid = '%s:%s' % (PID_PREFIX, print_id)
    from .forms import AnnotationForm, PersonForm, InscriptionForm
    PersonFormSet = formset_factory(PersonForm)
    InscriptionFormSet = formset_factory(InscriptionForm)
    if request.method == 'POST':
        form = AnnotationForm(request.POST)
        person_formset = PersonFormSet(request.POST, prefix='people')
        inscription_formset = InscriptionFormSet(request.POST, prefix='inscriptions')
        if form.is_valid() and person_formset.is_valid() and inscription_formset.is_valid():
            if request.user.first_name:
                annotator = u'%s %s' % (request.user.first_name, request.user.last_name)
            else:
                annotator = u'%s' % request.user.username
            annotation = Annotation.from_form_data(print_pid, annotator, form.cleaned_data, person_formset.cleaned_data, inscription_formset.cleaned_data)
            try:
                response = annotation.save_to_bdr()
                logger.info('%s added annotation %s for %s' % (request.user.username, response['pid'], print_id))
                return HttpResponseRedirect(reverse('specific_print', kwargs={'print_id': print_id}))
            except Exception as e:
                logger.error('%s' % e)
                return HttpResponseServerError('Internal server error. Check log.')
    else:
        inscription_formset = InscriptionFormSet(prefix='inscriptions')
        person_formset = PersonFormSet(prefix='people')
        form = AnnotationForm()

    image_link = 'https://%s/viewers/image/zoom/%s' % (BDR_SERVER, print_pid)
    return render(request, 'rome_templates/new_annotation.html',
            {'form': form, 'person_formset': person_formset, 'inscription_formset': inscription_formset, 'image_link': image_link})


def get_bound_edit_forms(annotation, AnnotationForm, PersonFormSet, InscriptionFormSet):
    person_formset = PersonFormSet(initial=annotation.get_person_formset_data(), prefix='people')
    inscription_formset = InscriptionFormSet(initial=annotation.get_inscription_formset_data(), prefix='inscriptions')
    form = AnnotationForm(annotation.get_form_data())
    return {'form': form, 'person_formset': person_formset, 'inscription_formset': inscription_formset}


def edit_annotation_base(request, image_pid, anno_pid, redirect_url):
    from .forms import AnnotationForm, PersonForm, InscriptionForm
    PersonFormSet = formset_factory(PersonForm)
    InscriptionFormSet = formset_factory(InscriptionForm)
    context_data = {}
    annotation = Annotation.from_pid(anno_pid)
    if request.method == 'POST':
        #this part here is similar to posting a new annotation
        form = AnnotationForm(request.POST)
        person_formset = PersonFormSet(request.POST, prefix='people')
        inscription_formset = InscriptionFormSet(request.POST, prefix='inscriptions')
        if form.is_valid() and person_formset.is_valid() and inscription_formset.is_valid():
            #update the annotator to be the person making this edit
            if request.user.first_name:
                annotator = u'%s %s' % (request.user.first_name, request.user.last_name)
            else:
                annotator = u'%s' % request.user.username
            annotation.add_form_data(annotator, form.cleaned_data, person_formset.cleaned_data, inscription_formset.cleaned_data)
            try:
                response = annotation.update_in_bdr()
                logger.info('%s edited annotation %s' % (request.user.username, anno_pid))
                return HttpResponseRedirect(redirect_url)
            except Exception as e:
                logger.error('%s' % e)
                return HttpResponseServerError('Internal server error. Check log.')
        else:
            context_data.update({'form': form, 'person_formset': person_formset, 'inscription_formset': inscription_formset})
    else:
        try:
            context_data.update(get_bound_edit_forms(annotation, AnnotationForm, PersonFormSet, InscriptionFormSet))
        except Exception as e:
            logger.error(u'loading data to edit %s: %s' % (anno_pid, e))
            return HttpResponseServerError('Internal server error.')

    image_link = 'https://%s/viewers/image/zoom/%s' % (BDR_SERVER, image_pid)
    context_data.update({'image_link': image_link})
    return render(request, 'rome_templates/new_annotation.html', context_data)


@login_required(login_url=reverse_lazy('rome_login'))
def edit_annotation(request, book_id, page_id, anno_id):
    anno_pid = '%s:%s' % (PID_PREFIX, anno_id)
    page_pid = '%s:%s' % (PID_PREFIX, page_id)
    return edit_annotation_base(request, page_pid, anno_pid, reverse('book_page_viewer', kwargs={'book_id': book_id, 'page_id': page_id}))


@login_required(login_url=reverse_lazy('rome_login'))
def edit_print_annotation(request, print_id, anno_id):
    anno_pid = '%s:%s' % (PID_PREFIX, anno_id)
    print_pid = '%s:%s' % (PID_PREFIX, print_id)
    return edit_annotation_base(request, print_pid, anno_pid, reverse('specific_print', kwargs={'print_id': print_id}))


@login_required(login_url=reverse_lazy('rome_login'))
def new_genre(request):
    from .forms import NewGenreForm
    if request.method == 'POST':
        form = NewGenreForm(request.POST)
        if form.is_valid():
            genre = form.save()
            return SimpleTemplateResponse('rome_templates/popup_response.html', {
                            'pk_value': escape(genre._get_pk_val()),
                            'value': escape(genre.serializable_value(genre._meta.pk.attname)),
                            'obj': escapejs(genre)})
    else:
        form = NewGenreForm()

    return render(request, 'rome_templates/new_record.html', {'form': form})


@login_required(login_url=reverse_lazy('rome_login'))
def new_role(request):
    from .forms import NewRoleForm
    if request.method == 'POST':
        form = NewRoleForm(request.POST)
        if form.is_valid():
            role = form.save()
            return SimpleTemplateResponse('rome_templates/popup_response.html', {
                            'pk_value': escape(role._get_pk_val()),
                            'value': escape(role.serializable_value(role._meta.pk.attname)),
                            'obj': escapejs(role)})
    else:
        form = NewRoleForm()

    #use the same template for genre and role
    return render(request, 'rome_templates/new_record.html', {'form': form})


@login_required(login_url=reverse_lazy('rome_login'))
def new_biography(request):
    from .forms import NewBiographyForm
    if request.method == 'POST':
        form = NewBiographyForm(request.POST)
        if form.is_valid():
            bio = form.save()
            return SimpleTemplateResponse('rome_templates/popup_response.html', {
                            'pk_value': escape(bio._get_pk_val()),
                            'value': escape(bio.serializable_value(bio._meta.pk.attname)),
                            'obj': escapejs(bio)})
    else:
        form = NewBiographyForm()

    #use the same template for genre and role
    return render(request, 'rome_templates/new_record.html', {'form': form})

