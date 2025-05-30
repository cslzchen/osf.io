<% from website import settings %>

<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>OSF | ${self.title()}</title>
    % if settings.GOOGLE_SITE_VERIFICATION:
        <meta name="google-site-verification" content="${settings.GOOGLE_SITE_VERIFICATION}" />
    % endif
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="${self.description()}">
    <meta http-equiv="X-UA-Compatible" content="IE=edge">
    <meta name="fragment" content="!">

    % if sentry_dsn_js:
    <script src="/static/vendor/bower_components/raven-js/dist/raven.min.js"></script>
    <script>
        Raven.config(${ sentry_dsn_js | sjson, n }, {}).install();
    </script>
    % else:
    <script>
        window.Raven = {};
        Raven.captureMessage = function(msg, context) {
            console.error('=== Mock Raven.captureMessage called with: ===');
            console.log('Message: ' + msg);
            console.log(context);
        };
        Raven.captureException = function(err, context) {
            console.error('=== Mock Raven.captureException called with: ===');
            console.log('Error: ' + err);
            console.log(context);
        };
    </script>
    % endif

    <!-- Metadata tags-->
    <meta name="dc.title" content="${self.title_meta()}" />
    <meta name="dc.type" content="collection" />
    <meta name="citation_title" content="${self.title_meta()}" />
    %if self.identifier_meta():
        <meta name="citation_doi" content="${self.identifier_meta()['doi']}" />
        <meta name="dc.identifier" content="${self.identifier_meta()['doi']}" />
        <meta name="dc.identifier" content="${self.identifier_meta()['ark']}" />
    %endif
    <meta name="citation_publisher" content="OSF" />
    %for institution in self.institutions_meta()[:10]:
        <meta name="citation_author_institution" content="${institution}" />
    %endfor
    %for rel in self.relations_meta():
        %if rel:
            <meta name="dc.relation" scheme="DCTERMS.URI" content="${rel}" />
        %endif
    %endfor
    <meta name="dc.abstract" content="${self.description_meta()}" />
    <meta name="dc.license" content="${self.license_meta()}" />
    <meta name="dc.datemodified" content="${self.datemodified_meta()}" />
    <meta name="dc.datesubmitted" content="${self.datecreated_meta()}" />
    <meta name="dc.publisher" content="OSF" />
    <meta name="dc.language" content="en" />
    <meta name="dc.identifier" content="${self.url_meta()}" />
    <meta name="citation_description" content="${self.description_meta()}" />
    <meta name="citation_public_url" content="${self.url_meta()}" />
    <meta name="citation_publication_date" content="${self.datecreated_meta()}" />

    <!-- Facebook display -->
    <meta property="og:ttl" content="3" />
    <meta property="og:site_name" content="OSF" />
    <meta property="og:url" content="${self.url_meta()}" />
    <meta property="og:title" content="${self.title_meta()}" />
    <meta property="og:description" content="${self.description_meta()}" />
    <meta property="og:image" content="${self.image_meta()}" />
    <meta property="og:image:type" content="image/png" />
    <meta property="og:image:width" content="1200" />
    <meta property="og:image:height" content="630" />
    <meta property="og:image:alt" content="OSF" />

    %for author, creator in list(zip(self.authors_meta()[:10], self.creator_meta()[:10])):
        <meta name="dc.creator" content="${creator}" />
        <meta name="citation_author" content="${author}" />
    %endfor
    %for tag in self.keywords_meta()[:10]:
        <meta name="citation_keywords" content="${tag}" />
        <meta name="dc.subject" content="${tag}" />
    %endfor

    <!-- Twitter display -->
    <meta name="twitter:card" content="summary">
    <meta name="twitter:site" content="@OSFramework">
    <meta name="twitter:creator" content="@OSFramework">

    <!-- Google Dataset -->
    % if node and node.get('is_public'):
        <script type="application/ld+json">
            ${google_dataset_tags| unicode, n }
        </script>
    % endif


    ${includes_top()}
    ${self.stylesheets()}
    <script src="${"/static/public/js/base-page.js" | webpack_asset}"></script>
    ${self.javascript()}

    <link href='https://fonts.googleapis.com/css?family=Open+Sans:400,600,300' rel='stylesheet' type='text/css'>

</head>
<body data-spy="scroll" data-target=".scrollspy">

    % if dev_mode:
        <div class="dev-mode-helper scripted" id="devModeControls">
        <div id="metaInfo" data-bind="visible: showMetaInfo">
            <h2>Current branch: <span data-bind="text: branch"></span></h2>
            <table>
                <thead>
                <tr>
                    <th>PR</th>
                    <th>Title</th>
                    <th>Date Merged</th>
                </tr>
                </thead>
                <tbody data-bind="foreach: pullRequests">
                    <tr>
                        <td>#<a data-bind="attr: {href: url}, text: number"></a></td>
                        <td data-bind="text: title"></td>
                        <td data-bind="text: mergedAt"></td>
                    </tr>
                </tbody>
            </table>
        </div>
        <style>
            #devmode {
                position:fixed;
                bottom:0;
                left:0;
                border-top-right-radius:8px;
                background-color:red;
                color:white;
                padding:.5em;
            }
        </style>
        <div id='devmode' data-bind='click: showHideMetaInfo'><strong>WARNING</strong>: This site is running in development mode.</div>
    </div>
    % endif

    ${self.nav()}
     ## TODO: shouldn't always have the watermark class
    ${self.content_wrap()}

<div class="footBanners">
    <div id="IEDepreciationBanner"  class="alert warningBanner">
        <div class="warningBannerText">
            OSF does not support the use of Internet Explorer. For optimal performance, please switch to another browser.
        </div>
        <div class="warningBannerAcceptBtn">
            <div class="btn btn-default" data-dismiss="alert" data-bind="click: accept" aria-label="Accept">Accept</div>
        </div>
    </div>
    % if not user_id:
    <div id="cookieBanner" class="alert warningBanner">
        <div id="cookieText" class="warningBannerText">
            This website relies on cookies to help provide a better user experience. By clicking Accept or continuing to use the site, you agree. For more information,
            see our <a href='https://github.com/CenterForOpenScience/cos.io/blob/master/PRIVACY_POLICY.md'>Privacy Policy</a>
            and information on <a href='https://github.com/CenterForOpenScience/cos.io/blob/master/PRIVACY_POLICY.md#7-types-of-information-we-collect'>cookie use</a>.
        </div>
        <div class="warningBannerAcceptBtn">
            <div class="btn btn-default" data-dismiss="alert" data-bind="click: accept" aria-label="Accept">Accept</div>
        </div>
    </div>
    <div id="footerSlideIn">
        <div class="container">
            <div class="row">
                <div class='col-sm-2 hidden-xs'>
                    <img class="logo" src="/static/img/circle_logo.png">
                </div>
                <div class='col-sm-10 col-xs-12'>
                    <a data-bind="click: dismiss" class="close" href="#">&times;</a>
                    <h1>Start managing your projects on the OSF today.</h1>
                    <p>Free and easy to use, the Open Science Framework supports the entire research lifecycle: planning, execution, reporting, archiving, and discovery.</p>
                    <div>
                        <a data-bind="click: trackClick.bind($data, 'Create Account')" class="btn btn-primary" href="${web_url_for('index')}#signUp">Create an Account</a>

                        <a data-bind="click: trackClick.bind($data, 'Learn More')" class="btn btn-primary" href="https://help.osf.io/article/384-managing-projects" target="_blank" rel="noreferrer">Learn More</a>
                        <a data-bind="click: dismiss">Hide this message</a>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>
% endif


    ${self.footer()}
        <%!
            import hashlib

            def user_hash(user_id):
                token = hashlib.md5()
                token.update(user_id)
                return token.hexdigest()
        %>

        <%!
            import datetime
            def create_timestamp():
                return str(datetime.datetime.utcnow())
        %>

        % if settings.GOOGLE_ANALYTICS_ID:
            <script>
            (function(i,s,o,g,r,a,m){i['GoogleAnalyticsObject']=r;i[r]=i[r]||function(){
            (i[r].q=i[r].q||[]).push(arguments)},i[r].l=1*new Date();a=s.createElement(o),
            m=s.getElementsByTagName(o)[0];a.async=1;a.src=g;m.parentNode.insertBefore(a,m)
            })(window,document,'script','//www.google-analytics.com/analytics.js','ga');

            ga('create', ${ settings.GOOGLE_ANALYTICS_ID | sjson, n }, 'auto', {'allowLinker': true});
            ga('require', 'linker');
            ga('linker:autoLink', ['centerforopenscience.org', 'cos.io'] );
            ga('set', 'dimension1', (${ user_id | sjson, n} != "") ? 'Logged in': 'Logged out');
            ga('set', 'dimension2', '${self.resource()}');
            ga('set', 'dimension3', '${self.public()}');
            ga('set', 'anonymizeIp', true);
            ga('send', 'pageview');
            </script>

        % else:
            <script>
                window.ga = function() {};
          </script>
        % endif

        % if settings.GOOGLE_TAG_MANAGER_ID:
            <!-- Google Tag Manager -->
            <script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':
            new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],
            j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
            'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
            })(window,document,'script','dataLayer',${ settings.GOOGLE_TAG_MANAGER_ID | sjson, n });</script>
            <!-- End Google Tag Manager -->
        % endif

        <script>
            // Mako variables accessible globally
            window.contextVars = $.extend(true, {}, window.contextVars, {
                osfURL: ${ osf_url if osf_url.endswith('/') else osf_url + '/' | sjson, n },
                waterbutlerURL: ${ waterbutler_url if waterbutler_url.endswith('/') else waterbutler_url + '/' | sjson, n },
                // Whether or not this page is loaded under osf.io or another domain IE: institutions
                isOnRootDomain: ${domain | sjson, n } === window.location.origin + '/',
                cookieName: ${ cookie_name | sjson, n },
                apiV2Domain: ${ api_v2_domain | sjson, n },
                apiV2Prefix: ${ api_v2_base | sjson, n },
                registerUrl: ${ api_url_for('register_user') | sjson, n },
                currentUser: {
                    id: ${ user_id | sjson, n },
                    locale: ${ user_locale | sjson, n },
                    timezone: ${ user_timezone | sjson, n },
                    entryPoint: ${ user_entry_point | sjson, n },
                    institutions: ${ user_institutions | sjson, n},
                    emailsToAdd: ${ user_email_verifications | sjson, n },
                },
                maintenance: ${ maintenance | sjson, n},
                analyticsMeta: {
                    pageMeta: {
                        routeName: ${page_route_name | sjson, n},
                    },
                },
                osfSupportEmail: ${osf_support_email | sjson, n },
                csrfCookieName: ${ csrf_cookie_name | sjson, n },
            });
        </script>

        % if settings.DATACITE_TRACKER_REPO_ID:
            <script>
                window.contextVars = $.extend(true, {}, window.contextVars, {
                    dataciteTrackerRepoId: ${ settings.DATACITE_TRACKER_REPO_ID | sjson, n },
                });
            </script>
        % endif

        ${self.javascript_bottom()}
    </body>
</html>


###### Base template functions #####

<%def name="nav()">
    <%namespace name="nav_helper" file="nav.mako" />
    ${nav_helper.nav(service_name='HOME', service_url=domain, service_support_url='https://help.osf.io/')}
</%def>

<%def name="title()">
    ### The page title ###
</%def>

<%def name="resource()"><%
    return 'n/a'
%> ### What resource is displayed on page ###
</%def>

<%def name="public()"><%
    return 'n/a'
%> ### What the public/private status of the resource displayed on page ###
</%def>

<%def name="container_class()">
    ### CSS classes to apply to the "content" div ###
</%def>

<%def name="description()">
    ### The page description ###
</%def>

<!-- Metadata tags-->
<%def name="description_meta()">
    Hosted on the Open Science Framework
</%def>

<%def name="title_meta()">
    ### The project title ###
</%def>

<%def name="institutions_meta()">
  ### The list of affiliated institutions ###
</%def>

<%def name="authors_meta()">
    ### The list of project contributors ###
</%def>

<%def name="creator_meta()">
    ### The list of project creators ###
</%def>

<%def name="datemodified_meta()">
    ### The project last modified date.
</%def>

<%def name="datecreated_meta()">
    ### The project creation date.
</%def>

<%def name="identifier_meta()">
    ### The project doi ###
</%def>

<%def name="license_meta()">
    ### The project license ###
</%def>

<%def name="keywords_meta()">
    ### The project tags ###
</%def>

<%def name="relations_meta()">
    ### The list of url for related nodes ###
</%def>

<%def name="category_meta()">
    ### The project category ###
</%def>

<%def name="url_meta()">
    ### The project canonical url ###
</%def>

<%def name="image_meta()">
    ### The project image url ###
</%def>
<!--Metadata tags-->

<%def name="stylesheets()">
    ### Extra css for this page. ###
</%def>

<%def name="javascript()">
    ### Additional javascript, loaded at the top of the page ###
</%def>

<%def name="content()">
    ### The body content. ###
</%def>

<%def name="javascript_bottom()">
    ### Javascript loaded at the bottom of the page ###
</%def>

<%def name="footer()">
    <%include file="footer.mako"/>
</%def>

<%def name="alert()">
    <%include file="alert.mako"/>
</%def>

<%def name="content_wrap()">
    <div class="watermarked" role="main">
        <div class="container ${self.container_class()}">
            % if status:
                ${self.alert()}
            % endif

            ### A11y Reccommended header
            <h1 style="display: none;">Main content</h1>
            ${self.content()}
        </div><!-- end container -->
    </div><!-- end watermarked -->
</%def>


<%def name="includes_top()">

    <!-- HTML5 shim and Respond.js for IE8 support of HTML5 elements and media queries -->
    <!-- WARNING: Respond.js doesn't work if you view the page via file:// -->
    <!--[if lt IE 9]>
      <script src="https://oss.maxcdn.com/html5shiv/3.7.2/html5shiv.min.js"></script>
      <script src="https://oss.maxcdn.com/respond/1.4.2/respond.min.js"></script>
      <script src="//cdnjs.cloudflare.com/ajax/libs/es5-shim/4.0.3/es5-shim.min.js"></script>
      <script src="//cdnjs.cloudflare.com/ajax/libs/es5-shim/4.0.3/es5-sham.min.js"></script>
    <![endif]-->

    <script src="https://cdnjs.cloudflare.com/ajax/libs/es6-shim/0.35.0/es6-shim.min.js"></script>

    % if settings.USE_CDN_FOR_CLIENT_LIBS:
        <link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/bootstrap/3.3.7/css/bootstrap.min.css" integrity="sha384-BVYiiSIFeK1dGmJRAkycuHAHRg32OmUcww7on3RYdg4Va+PmSTsz/K68vbdEjh4u" crossorigin="anonymous">
        <script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.2.1/jquery.min.js"></script>
        <script>window.jQuery || document.write('<script src="https://ajax.googleapis.com/ajax/libs/jquery/3.2.1/jquery.min.js">\x3C/script>')</script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/jqueryui/1.12.1/jquery-ui.min.js"></script>
        <script>window.jQuery.ui || document.write('<script src="https://ajax.googleapis.com/ajax/libs/jqueryui/1.12.1/jquery-ui.min.js">\x3C/script>')</script>
    % else:
        <script src="/static/vendor/bower_components/jquery/dist/jquery.min.js"></script>
        <script src="/static/vendor/bower_components/jquery-ui/jquery-ui.min.js"></script>
    % endif
    ## NOTE: We load vendor bundle  at the top of the page because contains
    ## the webpack runtime and a number of necessary stylesheets which should be loaded before the user sees
    ## content.
    <script src="${'/static/public/js/vendor.js' | webpack_asset}"></script>
</%def>
